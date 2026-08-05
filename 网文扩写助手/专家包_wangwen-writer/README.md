# 网文扩写助手 (Wangwen Writer)

按参考书《我穿书了 但是女频》的写作风格，把章节大纲扩写成完整网文正文的 Agent。风格基底基于三份参考稿（第 1-8 章 + 夜袭草船借箭）综合提取。

## 类型

Agent 型（单个 AI 专家）

## 功能

- **大纲扩写**：用户提供每章剧情大纲，扩写为 2000~3000 字完整章节
- **风格保持**：对话驱动、短段落、现代梗穿越、内心 OS 吐槽体、章末钩子
- **角色一致性**：严格按角色设定库写作，覆盖主角团、赵炎、众旧将、蒙军反派全部角色
- **伏笔延续**：密令、白衣女子、无金手指、刘监军动向等核心伏笔自动衔接
- **素材自包含**：风格指南、角色设定库、三份参考正文全部打包在技能 references 中，换电脑可直接使用

## 使用示例

- 我提供一章剧情大纲，你按风格指南扩写成网文正文
- 先给我看看风格指南和角色设定
- 把这段草稿改写成符合参考风格

## 头像

头像已自动生成在 `avatars/` 目录下。如需替换为自定义头像，要求：
- 格式：PNG（推荐）或 JPG
- 尺寸：512×512 px
- 大小：单张不超过 500KB

## 安装

将专家包目录放到专家目录下：

```
C:\Users\Administrator\.workbuddy\plugins\marketplaces\my-experts\plugins/wangwen-writer/
```

然后运行注册命令使其可见：

```bash
python3 scripts/register_expert.py <expert-dir>
```

## 打包分享

```bash
python3 scripts/package_expert.py <expert-dir> <output-dir>
```

## 结构说明

```
wangwen-writer/
├── .codebuddy-plugin/plugin.json   # 专家元数据
├── agents/wangwen-writer.md        # 专家核心指令（工作流+输出规范）
├── avatars/expert.png              # 头像
└── skills/wangwen-style/           # 写作风格技能
    └── references/
        ├── style-guide.md          # 写作风格指南（12 条扩写铁律+三份参考差异）
        ├── character-roster.md     # 角色设定库（含核心伏笔清单）
        ├── reference-text.md       # 参考1原文（第 1-5 章）
        ├── reference-text-2.md     # 参考2原文（第 6-8 章）
        └── reference-text-3.md     # 参考3原文（夜袭蒙营·草船借箭）
```

