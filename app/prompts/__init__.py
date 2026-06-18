from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str, **kwargs) -> str:
    path = _PROMPTS_DIR / f"{name}.j2"
    if not path.exists():
        raise FileNotFoundError(f"Prompt '{name}' não encontrado em {path}")
    text = path.read_text(encoding="utf-8").strip()
    if kwargs:
        return text.format(**kwargs)
    return text
