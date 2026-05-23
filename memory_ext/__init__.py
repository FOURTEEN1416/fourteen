"""
记忆增强模块 - mem0风格API，使用已有ChromaDB后端

设计思路:
  方案批判: 优化方案提议 pip install mem0ai，但mem0ai自带ChromaDB依赖，
           与项目已有的ChromaDB版本可能冲突。

  本方案: 提供与mem0相同的 add()/search()/get_all() API接口，
         但底层复用项目已有的ChromaDB（在 memory/ 模块中）。
         零新增依赖，零冲突风险。

  如果未来需要迁移到真实mem0:
    只需修改本模块的backend实现，API不变。

使用方式:
    from memory_ext import MemoryEnhancer

    enhancer = MemoryEnhancer(chroma_path="./data/chroma_db")

    # 自动提取记忆
    enhancer.add("用户说他喜欢喝咖啡", user_id="user_123")

    # 检索相关记忆
    results = enhancer.search("喝什么饮料", user_id="user_123")

    # 获取所有记忆
    all_memories = enhancer.get_all(user_id="user_123")
"""

from .enhancer import MemoryEnhancer

__all__ = ["MemoryEnhancer"]
