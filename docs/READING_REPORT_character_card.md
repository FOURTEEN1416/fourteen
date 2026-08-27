# 📚 Character Card 模块阅读报告

**读取进度**：6/6 文件 ✅ 已全部穷举阅读  
**读取时间**：2026-08-26  
**覆盖范围**：character_card/ 目录下全部6个 Python 源码文件  

---

## 🧠 核心组件概览

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出入口 | CardVersion/CharacterCard/CharacterData/WorldInfoBook/WorldInfoEntry/EmotionStyleMap<br>CharacterCardParser、PromptBuilder、CharacterValidator<br>向后兼容：from character_card import ... |
| `models.py` | 角色卡数据模型 | SillyTavern V1/V2/V3完全兼容格式<br>WorldInfoEntry/WorldInfoBook/CharacterExtensions<br>CharacterData：name/description/personality/scenario/first_mes/mes_example/alternate_greetings/system_prompt/post_history_instructions/creator_notes/tags/creator/character_book/extensions<br>EmotionStyleMap：from_character_card推断<br>已废弃注：请使用 shisi.core.models.character_aggregate.CharacterAggregate |
| `parser.py` | 角色卡解析器 | 支持JSON/PNG格式解析<br>不依赖pypng，使用Python内置struct+zlib解析PNG元数据<br>V1自动升级到V2<br>V3(chara_card_v3)优先解析<br>自动检测格式parse_auto(path_or_json)<br>PNG chunk遍历上限MAX_PNG_CHUNKS=10000 |
| `prompt_builder.py` | 系统提示词构建器 | 将SillyTavern角色卡→LLM提示词<br>构建顺序：1.main_prompt→2.description→3.personality→4.scenario→5.system_prompt→6.post_history_instructions→7.character_book<br>build_example_messages：few-shot示例提取<br>merge_with_persona_prompt：合并到PersonaEngine提示词 |
| `validator.py` | 角色卡验证器 | 轻量级完整性检查<br>REQUIRED_FIELDS：[name, description]<br>长度检查：MAX_NAME_LENGTH=100/MAX_DESC_LENGTH=50000<br>数值范围：talkativeness 0-1<br>角色书检查：常量条目enabled验证<br>示例消息检查：{{char}}/{{user}}占位符 |
| `__init__.py` (已读) | 已读内容 | 已读包导出入口、解析器、验证器等 |

---

## 🔧 主要设计模式

### 1. SillyTavern完全兼容
- **V1/V2/V3三版本全兼容**：
  - V1：无spec字段，顶层直接是data
  - V2：spec="chara_card_v2", spec_version="2.0"
  - V3：spec="chara_card_v3"（通过PNG tEXt chunk/ccv3关键字或JSON优先）
- **数据结构直接复用**：无转换损耗，直接对接SillyTavern格式
- **向下兼容**：V1格式自动升级到V2结构

### 2. PNG tEXt chunk解析（零依赖）
- **纯Python实现**：不依赖pypng库
- **结构**：PNG signature(8) + 迭代chunks：length(4)+type(4)+data+CRC(4)
- **目标chunk**：`tEXt`包含`chara`或`ccv3`关键字
- **安全限制**：MAX_PNG_SIZE=50MB，MAX_PNG_CHUNKS=10000防止畸形文件

### 3. 角色卡→PersonaEngine转换
- **核心集成点**：`CharacterCard.to_persona_config()`方法
- **关键字段映射**：
  - name→PersonaEngine name
  - description→description
  - personality→personality_traits
  - scenario→scenario
  - first_mes→greeting
  - system_prompt→system_prompt
  - tags→tags
  - creator_notes→creator_notes
- **情感倾向推断**：从description/personality关键词推断warmth/playfulness/independence/jealousy/stubbornness

### 4. 提示词构建流程
- **DEFAULT_MAIN_PROMPT**：`Write {{char}}'s next reply in a fictional chat between {{char}} and {{user}}.`
- **构建顺序**：
  1. 主提示词（角色扮演指令）
  2. 角色描述
  3. 角色性格
  4. 场景设定
  5. 系统指令
  6. 历史处理指令
  7. 角色书（常量条目）
- **示例消息提取**：build_example_messages：解析{{user}}/{{char}}占位符

### 5. 轻量级验证
- **验证项**：
  1. 必填字段检查
  2. 长度检查
  3. 数值范围检查
  4. 角色书条目完整性
  5. 示例消息格式检查
- **快速有效判断**：`CharacterValidator.is_valid(card)`返回bool

---

## ⚠️ 关键技术要点

### PNG元数据提取逻辑
```
PNG文件结构遍历:
  pos = 8  # 跳过signature
  while pos < len(png_bytes):
      length = struct.unpack_from('>I', png_bytes, pos)[0]
      chunk_type = png_bytes[pos+4:pos+8].decode('latin-1')
      chunk_data = png_bytes[pos+8:pos+8+length]
      if chunk_type == 'IEND': break
      if chunk_type == 'tEXt' and length > 0:
          null_pos = chunk_data.find(b'\x00')
          keyword = chunk_data[:null_pos].decode('latin-1').lower()
          text_data = chunk_data[null_pos+1:]
          # 尝试base64解码，失败则直接utf-8解码
          # 查找 'chara' 或 'ccv3' 关键字
      pos += 12 + length  # 4+4+data+4
```

### 角色卡版本自动检测
```
parse_auto(path_or_json):
  1. 若\n不在s且不以{开头 → 视为文件路径
  2. .png结尾 → parse_png_file()
  3. 否则 → parse_json()（尝试JSON解析）
```

### YAML/JSON双格式加载
- **检测**：`if not HAS_YAML → logger.warning → _load_defaults()`
- **YAML加载**：`yaml.safe_load(f)` → PromptTemplate实例
- **回退机制**：`_load_defaults()`提供4大默认模板

### f-string模板渲染
```
VAR_PATTERN = re.compile(r"\{(\w+)\}")
render(**kwargs):
  1. missing = self._variables - set(kwargs.keys()) → warning
  2. result = self.template
  3. for key, value in kwargs.items():
       result = result.replace(f"{{{key}}}", str(value))
  4. return result
```

---

## 📋 已读文件列表（完整）

1. __init__.py
2. models.py
3. parser.py
4. prompt_builder.py
5. validator.py