import subprocess
import re
import os
from .base import Capability, CapabilityResult

class BrowserNavigate(Capability):
    """
    Navigates to a URL using Obscura.
    """
    name = "browser.navigate"
    input_schema = {
        "url": "str"
    }

    def execute(self, inputs: dict) -> CapabilityResult:
        url = inputs.get("url")
        if not url:
            return CapabilityResult(success=False, output={}, error="Missing 'url'")
        
        # Ensure URL starts with protocol
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        obscura_path = r"E:\obscura\obscura.exe"
        try:
            # For obscura, navigate is just fetch. 
            # We return success if we can reach it.
            res = subprocess.run([obscura_path, "fetch", url], capture_output=True, text=True, timeout=20)
            if res.returncode == 0:
                return CapabilityResult(success=True, output={"url": url, "status": "Navigated successfully"})
            return CapabilityResult(success=False, output={}, error=res.stderr)
        except Exception as e:
            return CapabilityResult(success=False, output={}, error=str(e))

class BrowserRead(Capability):
    """
    Reads and distills a web page using the Obscura headless browser.
    """
    name = "browser.read"
    input_schema = {
        "url": "str",
        "clean": "bool (optional, default True)"
    }

    def execute(self, inputs: dict) -> CapabilityResult:
        url = inputs.get("url")
        if not url:
            return CapabilityResult(success=False, output={}, error="Missing 'url'")
        
        clean = inputs.get("clean", True)
        use_standby = inputs.get("use_standby", True)
        
        # 1. Try Primary: Obscura (Lightweight)
        obscura_path = r"E:\obscura\obscura.exe"
        try:
            res = subprocess.run([obscura_path, "fetch", url], capture_output=True, text=True, timeout=20)
            if res.returncode == 0 and len(res.stdout.strip()) > 200: # Ensure we got actual content
                content = self._distill(res.stdout)
                return CapabilityResult(success=True, output={"content": content, "url": url, "engine": "obscura"})
        except Exception as e:
            if not use_standby:
                return CapabilityResult(success=False, output={}, error=f"Obscura failed: {str(e)}")

        # 2. Try Standby: Vercel Agent Browser (Powerful/Chromium)
        if use_standby:
            try:
                # Open and take snapshot
                subprocess.run(["agent-browser", "open", url], capture_output=True, text=True, timeout=30)
                res = subprocess.run(["agent-browser", "snapshot"], capture_output=True, text=True, timeout=15)
                
                if res.returncode == 0:
                    # agent-browser output is already "clean" and has refs, so we just pass it through
                    return CapabilityResult(
                        success=True, 
                        output={
                            "content": res.stdout, 
                            "url": url, 
                            "engine": "vercel_agent_browser",
                            "note": "Obscura failed or returned low content; switched to Vercel Agent Browser standby."
                        }
                    )
            except Exception as e:
                return CapabilityResult(success=False, output={}, error=f"Both Obscura and Vercel standby failed: {str(e)}")

        return CapabilityResult(success=False, output={}, error="Browsing failed.")

    def _distill(self, html: str) -> str:
        """
        Strips HTML noise and injects "Pseudo-Snapshot" labels [@e1, @e2...] 
        for interactive elements to help local non-vision models.
        """
        # 1. Strip heavy noise blocks first
        html = re.sub(r'<(script|style|header|footer|nav|iframe|svg|canvas).*?>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
        
        # 2. Extract and label interactive elements
        elements = []
        counter = 1
        
        def capture_interactive(match):
            nonlocal counter
            tag = match.group(1).lower()
            attrs = match.group(2)
            
            # Use group(3) only if it exists (paired tags)
            inner = ""
            if len(match.groups()) >= 3 and match.group(3):
                inner = re.sub(r'<.*?>', '', match.group(3)).strip()
            
            ref = f"[@e{counter}]"
            counter += 1
            
            if tag == 'a':
                href = re.search(r'href=["\'](.*?)["\']', attrs)
                link = href.group(1) if href else "no-link"
                return f" {ref} [LINK: {inner} (URL: {link})] "
            elif tag == 'button':
                return f" {ref} [BUTTON: {inner}] "
            elif tag == 'input':
                itype_match = re.search(r'type=["\'](.*?)["\']', attrs)
                itype = itype_match.group(1) if itype_match else "text"
                
                placeholder_match = re.search(r'placeholder=["\'](.*?)["\']', attrs)
                name_match = re.search(r'name=["\'](.*?)["\']', attrs)
                
                label = placeholder_match.group(1) if placeholder_match else (name_match.group(1) if name_match else itype)
                return f" {ref} [INPUT: {label} ({itype})] "
            return match.group(0)

        # 1. Handle paired tags: <a> and <button>
        html = re.sub(r'<(a|button)\b([^>]*)>(.*?)</\1>', capture_interactive, html, flags=re.DOTALL | re.IGNORECASE)
        
        # 2. Handle <input> tags (both self-closing and paired)
        html = re.sub(r'<(input)\b([^>]*)/?>(?:</input>)?', capture_interactive, html, flags=re.IGNORECASE)

        # 3. Structural cleanup
        html = re.sub(r'<(p|br|li|h[1-6]).*?>', '\n', html, flags=re.IGNORECASE)
        text = re.sub(r'<.*?>', ' ', html)
        
        # 4. Final Whitespace Polish
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n\n', text)
        
        return text.strip()

class BrowserSearch(Capability):
    """
    Performs a web search via Obscura.
    """
    name = "browser.search"
    input_schema = {
        "query": "str"
    }

    def execute(self, inputs: dict) -> CapabilityResult:
        query = inputs.get("query")
        if not query:
            return CapabilityResult(success=False, output={}, error="Missing 'query'")
        
        search_url = f"https://duckduckgo.com/html/?q={query.replace(' ', '+')}"
        reader = BrowserRead()
        return reader.execute({"url": search_url})
