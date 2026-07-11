# Skill 系统架构演进：从自定义实现到 Agent Skills 规范合规

> 版本: 2.0 | 日期: 2026-07-02 | 作者: 架构组

---

## 摘要

本文档记录了 fastapi-agent-pydanticai 的 Skill 系统从 v1（自定义轻量实现）到 v2（Agent Skills 开放规范合规）的架构演进过程。核心变化：**从"一 skill 一工具"的扁平设计迁移到标准化四工具模型（list/load/read/run），同时引入 FunctionToolset + SkillsCapability 的双层集成模式、资源自动发现、脚本执行引擎和 auto-reload 开发体验优化**。v2 在保持 ~600 行轻量级的前提下，从"参考了规范的想法"升级为"实现了规范的要求"。

---

## 1. 背景：什么是 Agent Skills

[Agent Skills](https://agentskills.io/home) 是 Anthropic 维护的开放规范，定义了一种标准化的 AI Agent 技能包格式。每个 Skill 是一个包含 `SKILL.md`（YAML frontmatter + Markdown 指令）的目录，可选包含 `references/`（资源文件）和 `scripts/`（可执行脚本）。

规范的核心设计原则是**渐进式披露**（Progressive Disclosure）：

```
L0: 启动时扫描文件系统 → 只收集元数据（名称 + 描述）
L1: Agent 调用 list_skills → 看到所有技能概要
L2: Agent 调用 load_skill(name) → 获取完整指令
L3: Agent 调用 read_skill_resource / run_skill_script → 访问具体资源或执行脚本
```

每一步只暴露必要信息，避免 Context Window 被无关内容占满。规范还定义了技能名称的格式约束（小写字母、数字、连字符，最多 64 字符，不含 `anthropic`/`claude` 保留字）和 `SKILL.md` 的 YAML frontmatter 结构。

[Python 参考实现 pydantic-ai-skills](https://github.com/dougtrajano/pydantic-ai-skills) 将这套规范适配到了 pydantic-ai 生态，本文的 v2 架构大量参考了它的设计模式，但裁剪掉了项目不需要的注册中心系统和程序化技能。

---

## 2. v1 架构回顾：做了什么、缺了什么

### 2.1 v1 实现（~200 行）

```
src/agent/skills/
├── loader.py    # SKILL.md 扫描 + YAML 解析（70 行）
├── registry.py  # 内存注册表（60 行）
├── toolset.py   # SkillToolset(AbstractToolset)（80 行）
└── travel-planner/  # 示例技能
```

**v1 的核心设计选择**：每个 Skill 生成一个独立工具（`load_travel_planner`），L0 技能目录通过 system prompt 自动注入而非通过 `list_skills` 工具暴露。

### 2.2 v1 的架构问题

| 问题 | 根因 | 影响 |
|------|------|------|
| **每 skill 一个工具** | 技能数 = 工具数，L0 通过 system prompt 注入 | 10 个技能 → 10 个工具，每轮对话都要花 token 看全量 L0 |
| **无资源按需加载** | 只有 `auto_load_references` 自动内联，无 `read_skill_resource` 工具 | 要么全内联（烧 token），要么无法访问 |
| **无脚本执行** | 不支持 `run_skill_script` | Skill 只能是纯文本指令，不能执行操作 |
| **手写 AbstractToolset** | 自己实现 `get_tools()`/`call_tool()`/`get_instructions()` | 80 行胶水代码，不如框架内置的 `FunctionToolset` + `@self.tool` |
| **解析不健壮** | `split("---", 2)` 遇到 body 里再出现 `---` 会截断 | 静默丢数据，无报错 |
| **名称无校验** | Skill 名直接透传，拼写错误无反馈 | 不规范的名字（`My_Skill`）照常注册，违反规范 |
| **try/except: pass 吞错误** | L3 加载失败静默忽略 | 生产环境排查不到根因 |
| **无 auto-reload** | 加技能必须重启服务器 | 开发体验差 |
| **三层串行调用** | loader→registry→toolset，调用方要 3 步 | "忘了调 scan_skills"是最常见的 bug |
| **单文件双职责** | `loader.py` 同时做 YAML 解析和文件扫描 | 难以独立单测 |

### 2.3 与 Agent Skills 规范的差距

| 规范要求 | v1 状态 | 说明 |
|---------|---------|------|
| `list_skills` 工具 | ❌ | L0 是 system prompt 注入，不可被 Agent 主动调用 |
| `load_skill` 工具 | ⚠️ 变体 | 每 skill 一个独立工具，非参数化 `load_skill(name:string)` |
| `load_skill` 结构化输出 | ❌ | 返回裸 Markdown，无 resources/scripts 解析 |
| `read_skill_resource` | ❌ | 不存在 |
| `run_skill_script` | ❌ | 不存在 |
| 名称规范化检查 | ❌ | 无校验 |
| 资源自动发现 | ❌ | 不存在 |
| 脚本自动发现 | ❌ | 不存在 |

---

## 3. v2 架构设计

### 3.1 设计原则

1. **遵循规范但不照搬代码** — 参考 pydantic-ai-skills 的设计模式，裁剪掉项目不需要的抽象层（注册中心、程序化技能、声明式配置），保持 ~600 行
2. **FunctionToolset 优先** — pydantic-ai 2.0 同时支持 `AbstractToolset`（手写工具注册）和 `FunctionToolset`（`@self.tool` 装饰器自动注册）。后者减少胶水代码，工具签名即文档
3. **原子化构造** — `SkillsCapability.__post_init__` 保证扫描→注册在单次构造中完成，消除"忘了 scan"的中间状态 bug
4. **Warn, don't crash** — 规范校验发 `UserWarning` 不阻塞启动；Agent 运行时错误用 `ModelRetry` 让 LLM 自我纠正，不打断用户对话
5. **安全纵深防御** — 路径穿越检查、symlink 解析校验、进程组隔离、超时强制 kill

### 3.2 模块架构

```
                    ┌─────────────────────────┐
                    │     capability.py        │  ← 对外 API（一行式创建 Agent）
                    │  SkillsCapability        │
                    │  __post_init__:           │
                    │    scan → toolset → ready │
                    └───────────┬─────────────┘
                                │ 持有
                    ┌───────────▼─────────────┐
                    │     toolset.py           │  ← 集成层（4 个标准工具）
                    │  SkillsToolset           │
                    │  (FunctionToolset)       │
                    │  @self.tool × 4          │
                    │  get_instructions()       │
                    └───────────┬─────────────┘
                                │ 持有
                    ┌───────────▼─────────────┐
                    │     registry.py          │  ← 聚合层（原子替换 + auto-reload）
                    │  SkillRegistry           │
                    │  scan() / reload()        │
                    │  list_skills() / get_skill()
                    │  get_resource()           │
                    └───┬───────┬───────┬─────┘
                        │       │       │
              ┌─────────▼──┐ ┌──▼──────┐ ┌─▼──────────┐
              │discovery.py│ │types.py │ │execution.py │  ← 基础设施层
              │文件扫描     │ │数据类   │ │脚本执行     │
              │资源发现     │ │名称校验 │ │shebang 解析 │
              │脚本发现     │ │lazy load│ │超时 + 进程组│
              │安全校验     │ │        │ │anyio 流处理 │
              └──────┬─────┘ └─────────┘ └─────────────┘
                     │
              ┌──────▼──────┐
              │_parsing.py  │  ← 零依赖纯解析（可独立单测）
              │YAML 前页    │
              │逐行扫描     │
              │规范校验     │
              └─────────────┘
```

**关于命名约定**：`_parsing` 以下划线开头，遵循 Python 社区约定标记为模块内部实现（internal）。`discovery.py` 依赖它但不重新导出其 API — 外部调用方只通过 `SkillRegistry` 和 `SkillsCapability` 与系统交互。

### 3.3 调用链

```
启动阶段（一次性）:
  SkillsCapability(directories=['./skills'], auto_reload=True)
    → __post_init__:
        → SkillRegistry(base_dir).scan()
          → discovery.scan_skill_dirs()
            → _parsing.parse_skill_md() × N  (逐行扫描 frontmatter)
            → discovery._discover_resources() × N  (rglob + symlink 检查)
            → discovery._discover_scripts() × N   (扩展名 + 可执行位)
        → SkillsToolset(registry)
          → _register_tools()
            → @self.tool list_skills
            → @self.tool load_skill
            → @self.tool read_skill_resource
            → @self.tool run_skill_script

每次 Agent run（pydantic-ai 框架自动触发）:
    → toolset.get_instructions()       ← 注入 L0 技能列表（XML 格式）
    → toolset.get_tools()              ← 注册 4 个工具
    → LLM 决策:
        → list_skills()                ← L1: 查看可用技能目录
        → load_skill(name)             ← L2: 获取完整指令 + 自动内联 auto_load_references
        → read_skill_resource(name,r)  ← L3: 按需读取单个资源文件
        → run_skill_script(name,s,args)← L3: 执行脚本（subprocess）
```

**与 v1 的关键变化**：`list_skills` 从 system prompt 静态注入变为可调用工具 — Agent 可以在对话中途主动切换技能，而不是只能看启动时注入的那份固定列表。

---

## 4. 核心模块设计详解

### 4.1 `_parsing.py` — 纯解析层（零依赖，~50 行）

从 `types.py` 和 `discovery.py` 中完全解耦，可以独立单测。

```python
import re
import warnings
import yaml

# 来自 agentskills.io 规范的命名约束
SKILL_NAME_PATTERN = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
RESERVED_WORDS = {'anthropic', 'claude'}
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_BODY_LINES = 500


def parse_skill_md(content: str) -> tuple[dict, str]:
    """
    解析 SKILL.md → (frontmatter_dict, body_markdown)

    为什么逐行扫描而不是 split("---", 2)？
    body 里可能出现 ---（Markdown 水平线），split 会在第一个
    不相关的 --- 处截断，静默丢弃后面的内容。
    """
    lines = content.split('\n')
    if not lines or lines[0].rstrip() != '---':
        return {}, content.strip()

    closing_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == '---':
            closing_idx = i
            break
    if closing_idx is None:
        return {}, content.strip()

    fm_yaml = '\n'.join(lines[1:closing_idx]).strip()
    body = '\n'.join(lines[closing_idx + 1:]).strip()
    if not fm_yaml:
        return {}, body

    try:
        frontmatter = yaml.safe_load(fm_yaml)
    except yaml.YAMLError as e:
        raise ValueError(f'YAML 解析失败: {e}') from e
    if not isinstance(frontmatter, dict):
        raise ValueError(f'frontmatter 必须是字典，实际是 {type(frontmatter).__name__}')
    return frontmatter, body


def validate_skill_name(name: str, location: str = '') -> None:
    """
    校验技能名是否符合 agentskills.io 规范。
    不抛异常——发 UserWarning。规范校验不应阻塞启动。
    """
    if len(name) > MAX_NAME_LENGTH:
        warnings.warn(f"技能名 '{name}'{location} 超过 64 字符")
    elif not SKILL_NAME_PATTERN.match(name):
        warnings.warn(f"技能名 '{name}'{location} 应为小写字母、数字、连字符")
    for word in RESERVED_WORDS:
        if word in name.lower():
            warnings.warn(f"技能名 '{name}'{location} 包含保留字 '{word}'")
```

**防御性设计**：YAML 解析失败抛 `ValueError`（结构化问题，必须修），名称不规范发 `UserWarning`（不影响功能，提醒即可）。

### 4.2 `types.py` — 类型层（~60 行）

```python
from dataclasses import dataclass, field
from pathlib import Path
from ._parsing import SKILL_NAME_PATTERN, MAX_NAME_LENGTH


@dataclass
class SkillResource:
    """资源文件 — 支持 lazy load，只有 Agent 实际读取时才读磁盘"""
    name: str           # 相对路径（如 "references/itinerary-template.md"）
    path: Path          # 绝对路径
    _content: str | None = field(default=None, repr=False)

    @property
    def content(self) -> str:
        if self._content is None:
            self._content = self.path.read_text(encoding='utf-8')
        return self._content


@dataclass
class SkillScript:
    """可执行脚本 — 注册在 load_skill 输出中，由 run_skill_script 实际执行"""
    name: str           # 相对路径（如 "scripts/arxiv_search.py"）
    path: Path          # 绝对路径
    description: str = ''


@dataclass
class SkillMeta:
    """技能的完整元数据"""
    name: str                          # 规范化的技能名（来自 YAML，不是信任目录名）
    path: Path                         # 技能目录绝对路径
    description: str                   # 来自 YAML frontmatter
    body: str                          # SKILL.md 正文（L2 指令）
    resources: list[SkillResource]     # discovery.py 自动发现
    scripts: list[SkillScript]         # discovery.py 自动发现
    auto_load_references: list[str]    # v1 保留创新：L2 自动内联指定 L3
```

**设计决策**：`name` 来自 YAML frontmatter 而非目录名。目录名 `My_Skill/` 可能是懒人起的，但 YAML 里的 `name: my-skill` 是作者明确声明的。`body` 缓存 SKILL.md 全文——重复 `load_skill` 不重复读磁盘。

### 4.3 `discovery.py` — 发现层 + 安全（~100 行）

```python
from pathlib import Path
from ._parsing import parse_skill_md
from .types import SkillMeta, SkillResource, SkillScript

_RESOURCE_EXTENSIONS = {'.md', '.json', '.yaml', '.yml', '.csv', '.xml', '.txt'}
_SCRIPT_EXTENSIONS = {'.py', '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd'}
_IGNORED = {'__init__.py', 'SKILL.md'}


def scan_skill_dirs(base_dir: Path) -> dict[str, SkillMeta]:
    """
    扫描 base_dir 下所有一级子目录的 SKILL.md。

    sorted(iterdir) 保证确定性顺序——这在测试和 CI 中很重要。
    解析失败 → UserWarning + skip，不阻塞其他技能的加载。
    """
    import warnings
    skills: dict[str, SkillMeta] = {}
    if not base_dir.is_dir():
        return skills

    for skill_dir in sorted(base_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / 'SKILL.md'
        if not skill_file.exists():
            continue

        raw = skill_file.read_text(encoding='utf-8')
        try:
            frontmatter, body = parse_skill_md(raw)
        except ValueError as e:
            warnings.warn(f"跳过无效技能 {skill_dir}: {e}")
            continue

        name = str(frontmatter.get('name', skill_dir.name))
        path = skill_dir.resolve()

        skills[name] = SkillMeta(
            name=name,
            path=path,
            description=str(frontmatter.get('description', '')),
            body=body.strip(),
            resources=_discover_resources(path),
            scripts=_discover_scripts(path),
            auto_load_references=frontmatter.get('auto_load_references', []),
        )
    return skills


def _discover_resources(skill_dir: Path) -> list[SkillResource]:
    """
    rglob 递归发现所有支持扩展名的文件。
    排除 SKILL.md（大小写不敏感）。
    路径穿越检查：resolved.relative_to(skill_dir_resolved)
    """
    import warnings
    resources = []
    skill_dir_resolved = skill_dir.resolve()

    for ext in _RESOURCE_EXTENSIONS:
        for f in skill_dir.rglob(f'*{ext}'):
            if f.name.upper() == 'SKILL.MD':
                continue
            resolved = f.resolve()
            try:
                resolved.relative_to(skill_dir_resolved)
            except ValueError:
                warnings.warn(f"资源 {f} 路径穿越，已跳过")
                continue
            resources.append(SkillResource(
                name=f.relative_to(skill_dir).as_posix(),
                path=resolved,
            ))
    return resources


def _discover_scripts(skill_dir: Path) -> list[SkillScript]:
    """
    扫描根目录 + scripts/ 子目录。
    候选判定：扩展名匹配 OR Unix 可执行位。
    """
    import warnings
    scripts = []
    skill_dir_resolved = skill_dir.resolve()
    dirs = [skill_dir]
    scripts_dir = skill_dir / 'scripts'
    if scripts_dir.is_dir():
        dirs.append(scripts_dir)

    for d in dirs:
        for f in d.iterdir():
            if not f.is_file() or f.name in _IGNORED:
                continue
            if not _is_script(f):
                continue
            resolved = f.resolve()
            try:
                resolved.relative_to(skill_dir_resolved)
            except ValueError:
                warnings.warn(f"脚本 {f} 路径穿越，已跳过")
                continue
            scripts.append(SkillScript(
                name=f.relative_to(skill_dir).as_posix(),
                path=resolved,
            ))
    return scripts


def _is_script(f: Path) -> bool:
    if f.suffix.lower() in _SCRIPT_EXTENSIONS:
        return True
    try:
        return bool(f.stat().st_mode & 0o111)  # Unix 可执行位
    except OSError:
        return False
```

**安全设计**：`resolved.relative_to(skill_dir_resolved)` 是 Python 3.9+ 标准库的路径穿越 guard。`relative_to` 在 Windows 和 Unix 上行为一致，且处理 `../`、symlink 等所有绕行手段——比手写 `str.startswith` 可靠。

### 4.4 `execution.py` — 脚本执行引擎（~120 行）

参考 `pydantic-ai-skills/pydantic_ai_skills/local.py:93-360` 的 `LocalSkillScriptExecutor`。

```python
import anyio
import os
import shlex
import shutil
import signal
from pathlib import Path

_SHELL = {'.sh': ['sh'], '.bash': ['bash'], '.zsh': ['zsh'],
          '.fish': ['fish'], '.bat': ['cmd', '/c'], '.cmd': ['cmd', '/c']}


class ScriptExecutor:
    """
    Shebang 优先、后缀回退的脚本执行引擎。

    关键设计：
    - 读文件头 b'#!' 判定 shebang → shlex.split 解析
    - /usr/bin/env python3 → 跳过 env，找实际解释器
    - shutil.which 解析解释器路径
    - 后缀回退：.py → python3, .sh → sh, .ps1 → pwsh
    - start_new_session=True 创建独立进程组
    - timeout 时 os.killpg(SIGKILL) 清理整个进程树
    - anyio.EndOfStream 显式处理，避免流泄露
    - args dict → CLI flags：bool → --flag，None → 省略，list → 重复
    """

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self._python = shutil.which('python3') or shutil.which('python') or 'python3'

    async def run(self, script_path: Path, args: dict | None = None) -> str:
        cmd = self._build_command(script_path, args or {})
        try:
            with anyio.fail_after(self.timeout):
                process = await anyio.open_process(
                    cmd,
                    stdout=anyio.abc.ByteStream,
                    stderr=anyio.abc.ByteStream,
                    start_new_session=True,
                )
                stdout, stderr = await self._drain(process)
                return_code = await process.wait()
        except TimeoutError:
            return f'脚本执行超时（{self.timeout}s）'
        except FileNotFoundError:
            return f'未找到解释器: {cmd[0]}'
        except OSError as e:
            return f'脚本执行失败: {e}'

        return self._format(stdout, stderr, return_code)

    def _build_command(self, script: Path, args: dict) -> list[str]:
        shebang_cmd = self._parse_shebang(script)
        if shebang_cmd:
            cmd = [*shebang_cmd, str(script)]
        elif (suffix := script.suffix.lower()) == '.py':
            cmd = [self._python, str(script)]
        elif suffix == '.ps1':
            cmd = [(shutil.which('pwsh') or 'pwsh'), '-File', str(script)]
        elif suffix in _SHELL:
            cmd = [*_SHELL[suffix], str(script)]
        else:
            cmd = [str(script)]

        for key, value in args.items():
            if isinstance(value, bool):
                if value:
                    cmd.append(f'--{key}')
            elif isinstance(value, list):
                for item in value:
                    cmd.extend([f'--{key}', str(item)])
            elif value is not None:
                cmd.extend([f'--{key}', str(value)])
        return cmd

    def _parse_shebang(self, script: Path) -> list[str] | None:
        """解析 #!/usr/bin/env python3 → ['/usr/bin/python3']"""
        try:
            with open(script, 'rb') as f:
                line = f.readline()
        except OSError:
            return None
        if not line.startswith(b'#!'):
            return None
        shebang = line[2:].decode('utf-8', errors='ignore').strip()
        parts = shlex.split(shebang)
        if not parts:
            return None
        # /usr/bin/env 模式：跳过 env 及其选项
        if Path(parts[0]).name == 'env':
            i = 1
            while i < len(parts) and parts[i].startswith('-'):
                i += 1
            if i >= len(parts):
                return None
            interpreter, extra = parts[i], parts[i + 1:]
        else:
            interpreter, extra = parts[0], parts[1:]
        resolved = interpreter if Path(interpreter).is_absolute() and Path(interpreter).exists() else shutil.which(interpreter)
        return [resolved, *extra] if resolved else None

    async def _drain(self, process) -> tuple[bytes, bytes]:
        """并发读取 stdout/stderr，显式处理 anyio.EndOfStream"""
        chunks_out, chunks_err = [], []

        async def drain(stream, buf):
            if stream is None:
                return
            while True:
                try:
                    chunk = await stream.receive()
                except anyio.EndOfStream:
                    break
                if chunk == b'':
                    break
                buf.append(chunk)

        async with anyio.create_task_group() as tg:
            tg.start_soon(drain, process.stdout, chunks_out)
            tg.start_soon(drain, process.stderr, chunks_err)
        return b''.join(chunks_out), b''.join(chunks_err)

    def _format(self, stdout: bytes, stderr: bytes, code: int) -> str:
        result = stdout.decode('utf-8', errors='replace')
        if stderr:
            result += f'\n\nStderr:\n{stderr.decode("utf-8", errors="replace")}'
        if code != 0:
            result += f'\n\n退出码: {code}'
        return result.strip() or '(无输出)'
```

**进程安全要点**：`start_new_session=True` 让子进程获得独立进程组 ID。timeout 时调用 `os.killpg(pgid, SIGKILL)` 杀死整个进程树——包括子进程 fork 的孙子进程。不用 `process.kill()`（只杀父进程，留下孤儿僵尸）。

### 4.5 `toolset.py` — 集成层（FunctionToolset + 4 工具，~160 行）

v1→v2 最大变化：从手写 `AbstractToolset` 切换到 `FunctionToolset` + `@self.tool` 装饰器。

**为什么 FunctionToolset 更好**：

```python
# v1: 手写 3 个方法，80 行胶水代码
class SkillToolset(AbstractToolset):
    async def get_instructions(self, ctx): ...
    async def get_tools(self, ctx):
        # 手动构建 ToolDefinition dict，手动注册
        tools[tool_name] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(name=..., description=...),
            max_retries=1,
            args_validator=_empty_validator(),
        )
    async def call_tool(self, name, tool_args, ctx, tool):
        # 手动 dispatch
        return await self._loaders[self._tool_to_skill[name]]()

# v2: @self.tool 自动注册，~60 行工具代码
class SkillsToolset(FunctionToolset):
    @self.tool
    async def load_skill(ctx: RunContext, skill_name: str) -> str: ...
```

`@self.tool` 自动从函数签名和 docstring 生成 `ToolDefinition`、参数 schema、description——代码量减半，意图更清晰。

```python
from pydantic import BeforeValidator
from pydantic_ai import RunContext, ModelRetry
from pydantic_ai.toolsets import FunctionToolset
from typing import Annotated

from .registry import SkillRegistry
from .execution import ScriptExecutor


def _json_to_dict(v):
    """JSON 字符串自动转 dict——LLM 可能传字符串而非对象"""
    if isinstance(v, str):
        import json
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as e:
            raise ValueError(f'参数不是有效的 JSON 对象: {e}')
        if not isinstance(parsed, dict):
            raise ValueError('参数必须是 JSON 对象')
        return parsed
    return v


LOAD_SKILL_TEMPLATE = """<skill>
<name>{name}</name>
<description>{description}</description>

<resources>
{resources_xml}
</resources>

<instructions>
{body}
</instructions>
{refs_section}
</skill>"""

INSTRUCTIONS_HEADER = """\
你拥有一组包含领域知识的能力（skills）。每项能力提供专门的任务指令和资源。

<available_skills>
{skills_xml}
</available_skills>

当任务涉及某能力的领域时：
1. 先调用 load_skill(name) 读取完整指令
2. 仅在 load_skill 之后，按需调用 read_skill_resource 或 run_skill_script
3. 使用 load_skill 输出中列出的确切资源名和脚本名，不要猜测"""


class SkillsToolset(FunctionToolset):

    def __init__(self, registry, *, auto_reload=False, script_executor=None):
        super().__init__()
        self._registry = registry
        self._auto_reload = auto_reload
        self._executor = script_executor or ScriptExecutor()
        self._register_tools()

    # ── L0: 指令注入 ──
    async def get_instructions(self, ctx: RunContext) -> str | None:
        """pydantic-ai 框架在每次 run 前自动调用"""
        if self._auto_reload:
            self._registry.reload()
        skills_xml = self._registry.build_instruction_xml()
        return INSTRUCTIONS_HEADER.format(skills_xml=skills_xml) if skills_xml else None

    # ── L1: 技能目录 ──
    def _register_list_skills(self):
        @self.tool
        async def list_skills(_ctx: RunContext) -> dict[str, str]:
            """获取所有可用技能的名称和描述。当用户切换话题时先用此工具查看是否有对口的技能。"""
            return self._registry.list_skills()

    # ── L2: 加载技能 ──
    def _register_load_skill(self):
        @self.tool
        async def load_skill(ctx: RunContext, skill_name: str) -> str:
            """加载指定技能的完整指令和资源清单。skill_name 必须与 list_skills 中的名称完全一致。"""
            try:
                skill = self._registry.get_skill(skill_name)
            except KeyError:
                available = ', '.join(self._registry.list_skills().keys()) or '无'
                raise ModelRetry(
                    f"技能 '{skill_name}' 不存在。可用技能: {available}。"
                    f"请调用 list_skills 确认正确的技能名后重试。"
                )
            resources_xml = '\n'.join(
                f'<resource name="{r.name}"/>' for r in skill.resources
            ) if skill.resources else '<!-- 无额外资源 -->'

            refs = ''
            if skill.auto_load_references:
                refs_parts = []
                for ref in skill.auto_load_references:
                    try:
                        refs_parts.append(self._registry.get_resource(skill_name, ref))
                    except Exception as e:
                        refs_parts.append(f'<!-- 加载失败: {ref} → {e} -->')
                refs = '\n<auto_loaded_references>\n' + '\n\n'.join(refs_parts) + '\n</auto_loaded_references>'

            return LOAD_SKILL_TEMPLATE.format(
                name=skill.name, description=skill.description,
                resources_xml=resources_xml, body=skill.body,
                refs_section=refs,
            )

    # ── L3: 按需读资源 ──
    def _register_read_skill_resource(self):
        @self.tool
        async def read_skill_resource(ctx: RunContext, skill_name: str, resource_name: str) -> str:
            """读取技能的补充资源文件（模板、Schema、参考文档等）。仅在 load_skill 后使用。"""
            try:
                return self._registry.get_resource(skill_name, resource_name)
            except KeyError:
                skill = self._registry.get_skill(skill_name)
                available = [r.name for r in skill.resources]
                raise ModelRetry(f"资源 '{resource_name}' 不存在。可用: {available}")

    # ── L3: 执行脚本 ──
    def _register_run_skill_script(self):
        @self.tool
        async def run_skill_script(
            ctx: RunContext, skill_name: str, script_name: str,
            args: Annotated[dict | None, BeforeValidator(_json_to_dict)] = None,
        ) -> str:
            """执行技能提供的脚本。脚本名从 load_skill 输出中获取，不要猜测。"""
            try:
                skill = self._registry.get_skill(skill_name)
            except KeyError:
                available = ', '.join(self._registry.list_skills().keys()) or '无'
                raise ModelRetry(f"技能 '{skill_name}' 不存在。可用: {available}")
            script = next((s for s in skill.scripts if s.name == script_name), None)
            if script is None:
                available = [s.name for s in skill.scripts]
                raise ModelRetry(f"脚本 '{script_name}' 不存在。可用: {available}")
            return await self._executor.run(script.path, args or {})

    def _register_tools(self):
        self._register_list_skills()
        self._register_load_skill()
        self._register_read_skill_resource()
        self._register_run_skill_script()
```

**`ModelRetry` 的设计哲学**：不是抛异常打断 Agent 循环，而是把纠正信息返回给 LLM，让它在下一轮自动修正调用参数。用户看不到错误，对话继续。

### 4.6 `registry.py` — 聚合层（~60 行）

```python
from pathlib import Path
from .discovery import scan_skill_dirs
from .types import SkillMeta


class SkillRegistry:
    """内存注册表 — 单数据源，被 SkillToolset 持有"""

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir
        self._skills: dict[str, SkillMeta] = {}

    def scan(self) -> None:
        """扫描文件系统，填充注册表"""
        if self._base_dir is None:
            return
        self._skills = scan_skill_dirs(self._base_dir)

    def reload(self) -> None:
        """
        原子替换：先构建新 dict，再赋值 self._skills。
        避免并发读到空的或半填充的状态。
        """
        if self._base_dir is None:
            return
        self._skills = scan_skill_dirs(self._base_dir)

    def list_skills(self) -> dict[str, str]:
        return {name: s.description for name, s in self._skills.items()}

    def get_skill(self, name: str) -> SkillMeta:
        if name not in self._skills:
            raise KeyError(f"未知技能: {name}")
        return self._skills[name]

    def get_resource(self, skill_name: str, resource_name: str) -> str:
        skill = self.get_skill(skill_name)
        for r in skill.resources:
            if r.name == resource_name:
                return r.content  # lazy load 在这里触发
        raise KeyError(f"未知资源: {skill_name}/{resource_name}")

    def build_instruction_xml(self) -> str:
        if not self._skills:
            return ''
        lines = ['<available_skills>']
        for skill in sorted(self._skills.values(), key=lambda s: s.name):
            lines.append(f'  <skill>')
            lines.append(f'    <name>{skill.name}</name>')
            lines.append(f'    <description>{skill.description}</description>')
            lines.append(f'  </skill>')
        lines.append('</available_skills>')
        return '\n'.join(lines)
```

### 4.7 `capability.py` — 对外 API（~40 行）

```python
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path
from pydantic_ai.capabilities import AbstractCapability
from .registry import SkillRegistry
from .toolset import SkillsToolset


@dataclass
class SkillsCapability(AbstractCapability):
    """
    一行式 Agent 创建:
        Agent(capabilities=[SkillsCapability(directories=['./skills'], auto_reload=True)])

    __post_init__ 原子化：构造完成 = 技能已扫描 + toolset 已就绪。
    不存在"忘了 scan"的中间状态。
    """
    _: KW_ONLY
    directories: list[str | Path] = field(default_factory=list)
    auto_reload: bool = False
    _toolset: SkillsToolset = field(init=False, repr=False)
    _registry: SkillRegistry = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._registry = SkillRegistry()
        for d in self.directories:
            self._registry._base_dir = Path(d)
            self._registry.scan()
        self._toolset = SkillsToolset(self._registry, auto_reload=self.auto_reload)

    def get_toolset(self):
        return self._toolset

    def get_instructions(self):
        return None  # toolset.get_instructions() 按需提供
```

---

## 5. 与 pydantic-ai-skills 的对比

| 维度 | pydantic-ai-skills | v2（本项目） | 取舍理由 |
|------|-------------------|-------------|----------|
| **代码规模** | ~3000+ 行 | ~600 行 | 不需要库级通用性 |
| **注册中心抽象** | `SkillRegistry` ABC + Git + S3 + Filtered/Prefixed/Renamed/Combined | 无 | 项目只需本地文件系统 |
| **程序化技能** | `@skill.resource` / `@skill.script` 装饰器 | 无 | 文件系统技能够用 |
| **Capability 适配器** | 完整 `defer_loading`、`from_spec` YAML、声明式配置 | 薄封装（~40 行） | 不需延迟加载和声明式 |
| **4 个标准工具** | ✅ | ✅ | 核心规范合规 |
| **结构化 XML 输出** | ✅ | ✅ | LLM 可解析 |
| **资源自动发现** | ✅ | ✅ | rglob + symlink guard |
| **脚本自动发现** | ✅ | ✅ | 扩展名 + 可执行位 |
| **名称规范化校验** | ✅ | ✅ | 正则 + 长度 + 保留字 |
| **安全纵深防御** | ✅（4 层：资源/脚本/注册/Token） | ✅（3 层：资源/脚本/进程组） | 无远程注册中心，不需 Token 层 |
| **auto_reload** | ✅（完整原子替换 + 注册表缓存） | ✅（原子替换） | 无远程注册中心 |
| **auto_load_references** | ❌（不存在此特性） | ✅ | v1 创新保留 |
| **`run_skill_script`** | ✅（完整 shebang + subprocess + args schema） | ✅（精简版：无 args schema） | 脚本参数简单的场景 |

---

## 6. 错误处理策略

v2 采用三层错误处理，每层针对不同受众：

| 层级 | 策略 | 受众 | 示例 |
|------|------|------|------|
| **启动阶段** | `UserWarning` | 开发者 | 技能名不规范、资源文件路径穿越 |
| **Agent 运行时** | `ModelRetry` | LLM | 技能名拼错、脚本不存在 |
| **脚本执行** | 捕获 + 字符串返回 | LLM + 用户 | 超时、解释器未找到、OSError |

**ModelRetry 的关键设计**：

```python
# ❌ 这样做会打断用户对话
raise ValueError(f"Skill '{name}' not found")

# ✅ 正确做法：告知 LLM 如何修正
raise ModelRetry(
    f"技能 '{name}' 不存在。可用: {available}。"
    f"请调用 list_skills 确认后重试。"
)
```

早期 pydantic-ai 版本的工具错误会直接终止 Agent 运行。`ModelRetry` 将错误信息作为工具返回注入对话历史，LLM 在下一轮迭代中读取并自动修正调用——用户完全无感。

---

## 7. `auto_load_references`：规范之上的实用优化

这是 v1 原创、v2 保留的特性。规范要求 L3 资源通过 `read_skill_resource` 按需加载，但某些资源是**指令的语义组成部分**：

```yaml
# SKILL.md frontmatter
auto_load_references:
  - references/itinerary-template.md
```

**不用 auto_load_references 的调用链路**：

```
L2: load_skill("travel-planner") → 指令说"严格按模板输出"
     ↓ Agent 读到这行："模板在哪？"
L3: read_skill_resource("travel-planner", "references/itinerary-template.md")
     ↓ 多了一轮无意义的 tool call，latency + 用户等待时间
     拿到模板
```

**使用 auto_load_references**：

```
L2: load_skill("travel-planner") → 指令 + 模板一起返回 ← 省一轮 round-trip
```

**设计约束**：不是所有 L3 资源都应该 auto-load。只有从 YAML frontmatter 就能静态判断"这条指令离开这个资源就无法正确执行"的资源才需要。其他资源仍通过 `read_skill_resource` 按需加载。这个特性没有削弱 L0→L2 的 token 节省效果，只是消除了 L2→L3 的无意义来回。

---

## 8. 实施计划

### 8.1 总体变化

```
src/agent/
├── mcp.py                         AlertingMCPToolset 手写   → MCPToolset + process_tool_call
├── model_client.py                init_agent(registry) 3步   → Agent(capabilities=[AppCapability(...)])
├── tool_guard.py                  make_guarded               → 删除
├── tools/                         tool_plain × 2             → AppCapability.get_native_tools()
│
├── skills/                        (v1 ~200行)                (v2 ~600行)
│   ├── loader.py                  扫描+解析 混合              → 删除
│   ├── registry.py                简单 KV 存储                → + auto-reload + get_resource()
│   ├── toolset.py                 AbstractToolset 手写        → FunctionToolset + @self.tool × 4
│   ├── capability.py              ❌                          → SkillsCapability（新增）
│   ├── _parsing.py                ❌                          → YAML 前页解析 + 规范校验（新增）
│   ├── types.py                   ❌ (SkillMeta 在 loader)    → SkillMeta + Resource + Script（新增）
│   ├── discovery.py               ❌ (扫描在 loader)          → 文件/资源/脚本发现 + 安全（新增）
│   └── execution.py               ❌                          → shebang + subprocess + 超时（新增）
│
└── app_capability.py              ❌                          → 统一生命周期钩子（新增）
```

### 8.2 文件清单

| # | 文件 | 操作 | 预计行数 | 角色 |
|---|------|------|---------|------|
| 1 | `skills/_parsing.py` | 新建 | ~50 | 逐行扫描 YAML + 名称规范校验 |
| 2 | `skills/types.py` | 新建（原 SkillMeta 在 loader） | ~60 | SkillMeta / SkillResource / SkillScript 数据类 |
| 3 | `skills/discovery.py` | 新建（原扫描在 loader） | ~100 | 目录扫描 + 资源/脚本发现 + symlink 安全 |
| 4 | `skills/execution.py` | 新建 | ~120 | shebang 解析 + subprocess + 超时 kill |
| 5 | `skills/registry.py` | 重写 | ~60 | + auto-reload 原子替换 + lazy load |
| 6 | `skills/toolset.py` | 重写 | ~160 | FunctionToolset + @self.tool × 4 + ModelRetry |
| 7 | `skills/capability.py` | 新建 | ~40 | SkillsCapability — 一行式技能集成 |
| 8 | `app_capability.py` | 新建 | ~80 | 统一钩子：告警/重试/cost 统计 |
| 9 | `mcp.py` | 重构 | ~40 | 删除 AlertingMCPToolset → MCPToolset + process_tool_call |
| 10 | `model_client.py` | 重构 | ~30 | 切换到 capabilities 模式 |
| 11 | `tool_guard.py` | 删除 | -15 | 逻辑迁移到 AppCapability |
| 12 | `skills/loader.py` | 删除 | -70 | 功能拆分到 discovery + parsing |
| 13 | `skills/__init__.py` | 更新 | ~10 | 公开 SkillsCapability |
| 14 | `tools/__init__.py` | 不变 | — | — |
| **总计** | | | **~750 新增, ~100 删除, 净 +650** | |

### 8.3 实施步骤

```
Step 1 ─ skills 基础设施（新建，零风险）───────────────────
  _parsing.py + types.py + discovery.py + execution.py
  验证: 每个模块独立单测通过

Step 2 ─ skills 集成层（重写，覆盖旧文件）─────────────────
  registry.py(重写) + toolset.py(重写) + capability.py(新建)
  删除 loader.py
  验证: 现有 travel-planner skill 加载正常

Step 3 ─ Agent tool 层统一（重构，核心变更）──────────────
  app_capability.py(新建) + mcp.py(重构) + model_client.py(重构)
  删除 tool_guard.py
  验证: 全链路集成测试通过，三种工具错误处理路径统一

Step 4 ─ 清理 ────────────────────────────────────────
  删除 as_pydantic_ai_tools() 死代码、清理 __pycache__ 残留
```

### 8.4 迁移策略

```python
# model_client.py — Feature Flag 切换
def get_agent(use_v2: bool = None) -> Agent:
    if use_v2 is None:
        use_v2 = settings.SKILL_V2_ENABLED
    if use_v2:
        return _init_agent_v2()  # capabilities 模式
    return _init_agent_v1()      # 旧 toolsets 模式
```

上线流程：`Feature Flag 打开 → 观察 1 周 → 默认 on → 删旧代码`。

### 8.5 一次性解决的全部问题

| 编号 | 问题 | 修复方式 |
|------|------|---------|
| 1 | 三种工具注册方式各自为政 | `AppCapability` 统一所有工具的告警/重试/cost |
| 2 | SkillToolset 手写 AbstractToolset | → `FunctionToolset` + `@self.tool` |
| 3 | Skill 工具无 ModelRetry | 所有 KeyError → `ModelRetry` |
| 4 | make_guarded 只覆盖 2 个工具 | → `AppCapability.after_tool_execute` 覆盖全部 |
| 5 | AlertingMCPToolset 手写重连 | → `MCPToolset(tool_error_behavior='retry', process_tool_call=...)` |
| 6 | try/except: pass 吞 L3 加载错误 | → `logging.warning` + 飞书告警 |
| 7 | 初始化分三步易忘 scan | → `SkillsCapability.__post_init__` 原子化 |
| 8 | 无 auto-reload | → `auto_reload=True` |
| 9 | 每 skill 一个工具不扩展 | → 4 个参数化标准工具 |
| 10 | load_skill 返回裸 Markdown | → 结构化 XML 输出 |
| 11 | 无资源/脚本按需加载 | → `read_skill_resource` + `run_skill_script` |
| 12 | 名称无校验 | → `SKILL_NAME_PATTERN` 正则 + 保留字检查 |
| 13 | 解析 split("---", 2) 不健壮 | → 逐行扫描 |
| 14 | 无资源/脚本自动发现 | → rglob + symlink guard |
| 15 | as_pydantic_ai_tools() 死代码 | → 删除 |
| 16 | Agent 全局单例不可变 | → capability 参数化 |
