# Contributing / 贡献指南

[中文](#中文) | [English](#english)

---

## 中文

感谢你为 Transparent Company 贡献代码。

### 开始前
- 先阅读 `/Users/a/repos/transparent-company/README.md` 与 `/Users/a/repos/transparent-company/SKILLS.md`
- 使用 Python 3.11+ 环境（推荐直接用 Docker）
- 先运行测试，确保基线通过

### 开发流程
1. 从 `main` 拉取最新代码
2. 新建分支（建议前缀 `codex/`）
3. 进行最小必要修改，避免无关重构
4. 添加或更新测试
5. 本地通过测试后提交 PR

### 提交规范
- Commit message 用祈使句，明确改动目标
- 一个 commit 尽量只做一类改动
- PR 描述请包含：
  - 背景与目标
  - 主要修改点
  - 测试结果
  - 风险与回滚方案（如有）

### 代码与文档要求
- 货币字段使用 `int cents`，禁止 float
- 时间统一 UTC
- 新增接口需保持向后兼容，除非明确做 breaking change
- 帮助文档请保持中英双语

---

## English

Thanks for contributing to Transparent Company.

### Before You Start
- Read `/Users/a/repos/transparent-company/README.md` and `/Users/a/repos/transparent-company/SKILLS.md`
- Use Python 3.11+ (Docker is recommended)
- Run tests first to verify a clean baseline

### Development Flow
1. Sync latest `main`
2. Create a branch (recommended prefix: `codex/`)
3. Keep changes minimal and focused
4. Add or update tests
5. Open a PR after local verification

### Commit and PR Style
- Use imperative commit messages
- Prefer one logical change per commit
- PR description should include:
  - context and objective
  - key changes
  - test evidence
  - risk and rollback notes (if any)

### Code and Docs Requirements
- Use `int cents` for money; no float
- Use UTC for time
- Keep API backward compatibility unless explicitly introducing a breaking change
- Keep help docs bilingual (Chinese + English)
