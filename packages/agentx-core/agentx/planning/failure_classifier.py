class FailureType:
    TRANSIENT = "transient"
    LOGIC = "logic"
    EXTERNAL = "external"

def classify_failure(error_message: str) -> str:
    """Classifies a failure to route it to retry, repair, or escalate."""
    if not error_message:
        return FailureType.EXTERNAL
        
    err = error_message.lower()
    
    # Transient errors can be retried immediately (e.g. timeouts, rate limits)
    if any(k in err for k in ["timeout", "network", "connection", "rate limit", "temporarily"]):
        return FailureType.TRANSIENT
        
    # Logic errors trigger the local subtree repair engine
    elif any(k in err for k in ["not found", "syntax", "valueerror", "typeerror", "keyerror", "assertion", "logic", "missing"]):
        return FailureType.LOGIC
        
    # External errors escalate to human/global replanner
    else:
        return FailureType.EXTERNAL
