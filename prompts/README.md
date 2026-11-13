# 提示词（外置，改标准不改代码）

- `system.md`：通用评分框架、读者画像占位符 `{profile}`、日期 `{today}`、类别补充 `{category_guide}`、JSON 输出格式。
- `category/<分类>.md`：每个分类的权重与特殊规则（school / competition / tech / news）。

提示词内容的哈希会记在每条记录的 `prompt_version` 上，方便以后对比不同版本的打分效果。
改完后在「设置 → 模型」点“重新打分今天的条目”即可生效。

评分流程参考了 AIHOT（MIT）公开的精选思路（预筛 → 独立打分 → 结构化 → 规则出简报），提示词为本项目针对校园场景重写。
