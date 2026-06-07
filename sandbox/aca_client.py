"""Azure Container Apps dynamic sessions sandbox client."""
import httpx
from typing import Dict, Any, Optional

class ACASessionClient:
    def __init__(self, session_pool_endpoint: str, auth_token: str):
        self.endpoint = session_pool_endpoint.rstrip("/")
        self.auth_token = auth_token
        self.session_id: Optional[str] = None

    async def create_session(self) -> str:
        """Create a new sandbox session."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.endpoint}/sessions",
                headers={"Authorization": f"Bearer {self.auth_token}"},
            )
            response.raise_for_status()
            data = response.json()
            self.session_id = data["id"]
            return self.session_id

    async def execute_code(self, code: str, language: str = "python") -> Dict[str, Any]:
        """Execute code in the sandbox. Returns stdout, stderr, exit_code."""
        if not self.session_id:
            await self.create_session()

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.endpoint}/sessions/{self.session_id}/execute",
                headers={"Authorization": f"Bearer {self.auth_token}"},
                json={"code": code, "language": language}
            )
            response.raise_for_status()
            return response.json()

    async def cleanup(self):
        """Destroy the sandbox session."""
        if not self.session_id:
            return
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"{self.endpoint}/sessions/{self.session_id}",
                headers={"Authorization": f"Bearer {self.auth_token}"}
            )
        self.session_id = None
