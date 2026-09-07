#!/usr/bin/env python3
"""检查文档里引用的仓库内路径是否真的存在。

治的病：文档白纸黑字引用某个文件/脚本/目录，实物却不在仓库里——只活在某台机器
的本地副本上，或者从头到尾没被提交；换台机器后，引用就成了化石。

判据：**文档能指到的东西，仓库里必须有。** 现有的门都测不到这一条——
测试只证明"没改坏"，对"引用了一个不存在的东西"天生失明。

用法：
    python3 tools/check_doc_refs.py                 # 扫全仓，有缺失则退出码 1
    python3 tools/check_doc_refs.py docs tools      # 只扫指定目录
    python3 tools/check_doc_refs.py --json          # 机器可读
    python3 tools/check_doc_refs.py --list-ignored  # 看被忽略的引用，调误报用

忽略规则写在仓库根的 .docrefsignore（每行一个 glob，# 开头为注释）。

误报类型与处置：
  · 文档模板的占位路径（round-N、xxx、<名称>、……、示例等）自动跳过；它们不是
    对仓库实物的承诺。
  · Markdown 为展示而加的反斜杠（如 \\_raw/）先还原，再按真实路径解析。
  · 子项目源码引用依次在 SUBPROJECT_ROOTS 配置的目录下查找，避免把子项目相对
    路径误报为仓库根相对路径。
  · 文档树内的 archive/、meetings/ 等相对上级目录的引用会逐级向上解析。
  · 服务器运行态数据、明确不入库的历史回执才写入 .docrefsignore；每条必须说明
    原因，不能以忽略规则掩盖应提交的文件。
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

# [文字](路径) —— 排除图片语法之外的一切链接
LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# `行内代码` —— 只有长得像路径的才检查
CODE_RE = re.compile(r"`([^`\n]+)`")

# 一眼就不是仓库内路径的
SCHEME_RE = re.compile(r"^(https?|mailto|ftp|data|tel|ssh|git|file):", re.I)
# 命令行片段、占位符、通配符：出现这些就不当路径看
NOISE = ("*", "?", "$", "<", ">", "|", "{", "}", "%")

# 看起来像文件的扩展名（行内代码要有这个才算路径引用）
FILE_EXT = re.compile(r"\.(md|py|mjs|js|ts|tsx|json|sh|yaml|yml|toml|sql|html|css|txt|env|lock|cfg|ini)$", re.I)
# 只匹配路径组件中的明确占位符，避免把普通英文或中文文件名误判成示例。
PLACEHOLDER_COMPONENT_RE = re.compile(
    r"^(?:N+|x{2,}|X{2,}|YYYY(?:-MM(?:-DD)?)?|MM-DD|round-N|[<＜].+[>＞]|[.。…]{3,}|.*(?:示例|样例|占位).*)$",
    re.I,
)
ESCAPED_MARKDOWN_RE = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~])")
# 子项目的文档可省略此前缀。本仓没有独立子项目，保留空列表供以后按需配置。
SUBPROJECT_ROOTS: list[str] = []


def repo_root(start: Path) -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start, capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return start


def load_ignores(root: Path) -> list[str]:
    f = root / ".docrefsignore"
    if not f.exists():
        return []
    pats = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pats.append(line)
    return pats


def is_pathish(raw: str, *, from_code: bool) -> bool:
    """判断一个字符串该不该被当作仓库内路径检查。"""
    s = raw.strip()
    if not s or SCHEME_RE.match(s) or s.startswith("#"):
        return False
    if s.startswith(("~", "/")):          # 用户目录/绝对路径：不是仓库内引用
        return False
    if any(n in s for n in NOISE):
        return False
    if " " in s and from_code:            # 行内代码里带空格的多半是命令
        return False
    if from_code:
        # 行内代码要求更严：必须含 / 且有文件扩展名，或明确以 ./ 开头
        return ("/" in s and bool(FILE_EXT.search(s))) or s.startswith("./")
    return True                           # markdown 链接：只要不是上面那些就查


def normalise_ref(raw: str) -> str:
    """还原 Markdown 转义；保留原始字符串用于报告。"""
    return ESCAPED_MARKDOWN_RE.sub(r"\1", raw.strip())


def is_placeholder(ref: str) -> bool:
    """只跳过明确的模板/示例路径，避免吞掉真实缺失。"""
    target = ref.split("#", 1)[0].split("?", 1)[0].strip()
    for part in target.split("/"):
        if not part:
            continue
        # NNNN-title.md、YYYY-MM-DD-meeting.md 仍是模板，即使占位符只是文件名的一部分。
        stem = part.rsplit(".", 1)[0]
        if PLACEHOLDER_COMPONENT_RE.match(part) or re.match(r"^(?:N+|x{2,}|X{2,}|YYYY-MM-DD)(?:[-_].*)?$", stem, re.I):
            return True
    return False


def resolve(ref: str, md_file: Path, root: Path) -> Path | None:
    """按文档目录、其祖先、仓库根及配置的子根解析；任一命中即算存在。"""
    target = ref.split("#", 1)[0].split("?", 1)[0].strip()
    if not target:
        return None
    bases: list[Path] = []
    current = md_file.parent
    while True:
        bases.append(current)
        if current == root or current.parent == current:
            break
        current = current.parent
    bases.extend(root / subroot for subroot in SUBPROJECT_ROOTS)
    for base in dict.fromkeys(bases):
        p = (base / target)
        try:
            # ../ 可指向文档树祖先，但不能让仓库外的本地文件满足此质量门。
            if p.resolve().is_relative_to(root.resolve()) and p.exists():
                return p
        except OSError:
            continue
    return None


def git_tracked_markdown(root: Path, scan_dir: Path) -> list[Path]:
    """scan_dir 下受 git 管理的 .md（已跟踪 + 未忽略的未跟踪）；git 不可用时退回 rglob。"""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "-co", "--exclude-standard", "--", str(scan_dir.relative_to(root)) if scan_dir != root else "."],
            cwd=root, capture_output=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return [
            p for p in scan_dir.rglob("*.md")
            if not any(part in {".git", "node_modules", "repos", ".venv", "dist", "build"} for part in p.parts)
        ]
    return [root / rel for rel in out.decode("utf-8", "replace").split("\0") if rel.endswith(".md")]


def main() -> int:
    args = [a for a in sys.argv[1:]]
    as_json = "--json" in args
    list_ignored = "--list-ignored" in args
    args = [a for a in args if not a.startswith("--")]

    root = repo_root(Path.cwd())
    ignores = load_ignores(root)
    scan_dirs = [root / a for a in args] if args else [root]

    # 只看 git 管理（含未忽略的新文件）的文档：workspace/、.claude/ 等被 gitignore 的
    # 运行态目录里的 .md 不是仓库承诺，扫进来只会在真实检出里制造假红。
    md_files: list[Path] = []
    for d in scan_dirs:
        if d.is_file() and d.suffix == ".md":
            md_files.append(d)
        elif d.is_dir():
            md_files.extend(git_tracked_markdown(root, d))

    missing: list[dict] = []
    ignored: list[dict] = []
    checked = 0

    for md in sorted(set(md_files)):
        try:
            lines = md.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        in_fence = False
        for lineno, line in enumerate(lines, 1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:                      # 代码块内容不查（多是命令示例）
                continue
            cands: list[tuple[str, bool]] = [(m, False) for m in LINK_RE.findall(line)]
            cands += [(m, True) for m in CODE_RE.findall(line)]
            for raw, from_code in cands:
                ref = normalise_ref(raw)
                if is_placeholder(ref):
                    ignored.append({"file": str(md.relative_to(root)), "line": lineno,
                                    "ref": raw, "reason": "模板/示例占位路径"})
                    continue
                if not is_pathish(ref, from_code=from_code):
                    continue
                rel = str(md.relative_to(root))
                if any(fnmatch.fnmatch(ref, p) for p in ignores):
                    ignored.append({"file": rel, "line": lineno, "ref": raw,
                                    "reason": ".docrefsignore"})
                    continue
                checked += 1
                if resolve(ref, md, root) is None:
                    missing.append({"file": rel, "line": lineno, "ref": raw,
                                    "kind": "code" if from_code else "link"})

    if as_json:
        print(json.dumps({"checked": checked, "missing": missing,
                          "ignored": len(ignored)}, ensure_ascii=False, indent=2))
        return 1 if missing else 0

    if list_ignored:
        for i in ignored:
            print(f"  忽略 {i['file']}:{i['line']}  {i['ref']}")
        print(f"\n共忽略 {len(ignored)} 条")
        return 0

    print(f"扫描 {len(set(md_files))} 份文档，检查 {checked} 条仓库内引用"
          + (f"（忽略 {len(ignored)} 条）" if ignored else ""))
    if not missing:
        print("✅ 全部存在")
        return 0

    print(f"\n❌ {len(missing)} 条引用指向不存在的东西：\n")
    by_file: dict[str, list[dict]] = {}
    for m in missing:
        by_file.setdefault(m["file"], []).append(m)
    for f, items in sorted(by_file.items()):
        print(f"  {f}")
        for m in items:
            print(f"    :{m['line']}  {m['ref']}")
    print("\n处置：把实物提交进仓库，或修正引用，或写进 .docrefsignore（并注明理由）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
