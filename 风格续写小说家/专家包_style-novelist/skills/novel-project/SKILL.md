---
name: novel-project
description: "Templates and file conventions for long-form novel projects handled by the style continuation novelist. Provides the style profile template and story bible template that keep style extraction and plot-tracking output structurally consistent across projects and sessions."
---

# 小说项目管理技能

本技能为风格续写小说家提供小说项目落地所需的模板与文件约定。**每次分析参考文章、建立故事圣经、更新章节进度时，必须参照本技能中的模板**，保证产出结构稳定、可跨会话续用。

## 项目文件结构

```
novels/<小说名>/
├── style-profile.md      # 风格画像（Phase 1 产出）
├── story-bible.md        # 故事圣经（Phase 2 建立，每章更新）
└── chapters/
    ├── chapter-001.md
    ├── chapter-002.md
    └── ...
```

## 使用时机

- **风格学习**：用户喂参考文章时 → 按 `references/style-profile-template.md` 产出风格画像
- **故事设定**：用户给世界观/人物/大纲时 → 按 `references/story-bible-template.md` 建立故事圣经
- **章节写作前**：必须读取 style-profile.md + story-bible.md + 上一章结尾
- **章节写作后**：必须更新 story-bible.md（章节摘要、时间线、伏笔、人物状态）

## 模板文件

- `references/style-profile-template.md` — 风格画像模板（文体、用词、句式、视角、对话、描写、叙事、情绪、标点、锚点）
- `references/story-bible-template.md` — 故事圣经模板（人物卡、世界观、时间线、伏笔、章节摘要）

## 速记规则

- 风格画像必须含「风格锚点」章节（3-5 段原文摘录 + 出处），写作时逐条对照
- 故事圣经的伏笔清单必须分「已埋 / 待收 / 已收」三态，每次更新勾销或登记
- 新角色、新设定未获用户确认不得写入圣经
- 章节摘要每章 3-5 行，写清楚「本章发生了什么、人物状态变化、埋/收的伏笔」
