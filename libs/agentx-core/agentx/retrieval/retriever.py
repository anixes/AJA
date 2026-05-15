from typing import List, Dict, Any

def vector_search(query: str) -> List[Dict[str, Any]]:
    # Mock semantic search - would connect to vector DB
    return [{"content": f"Semantic context for '{query}'. Ensure safe execution.", "score": 0.85, "type": "semantic"}]

def keyword_search(query: str) -> List[Dict[str, Any]]:
    # Mock keyword search - would connect to elastic/BM25
    return [{"content": f"Keyword context for '{query}'. Remember rollback procedures.", "score": 0.9, "type": "keyword"}]

def merge(semantic: List[Dict[str, Any]], keyword: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Deduplicate and merge results
    merged = {item["content"]: item for item in semantic + keyword}
    return list(merged.values())

def retrieve(query: str) -> List[Dict[str, Any]]:
    """Hybrid retrieval combining semantic and keyword search."""
    semantic = vector_search(query)
    keyword = keyword_search(query)
    
    merged = merge(semantic, keyword)
    
    try:
        from agentx.retrieval.reranker import rerank
        return rerank(merged)
    except ImportError:
        return merged
