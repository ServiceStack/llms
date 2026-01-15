from .anthropic import install_anthropic
from .cerebras import install_cerebras
from .chutes import install_chutes
from .google import install_google
from .nvidia import install_nvidia
from .openai import install_openai
from .openrouter import install_openrouter
from .zai import install_zai


def install(ctx):
    install_anthropic(ctx)
    install_cerebras(ctx)
    install_chutes(ctx)
    install_google(ctx)
    install_nvidia(ctx)
    install_openai(ctx)
    install_openrouter(ctx)
    install_zai(ctx)


__install__ = install
