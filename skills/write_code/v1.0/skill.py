"""Write code skill — generates code against the contract."""
from typing import Dict

async def write_code(contract: str, schema: str, language: str = "python") -> Dict[str, str]:
    """
    Generate code against the OpenAPI contract.

    Returns:
        {"file_path": content, ...}
    """
    # TODO: Call LLM to generate code
    return {"src/main.py": "# Generated code\nprint('Hello World')"}
