from .anthropic import install_anthropic
from .chutes import install_chutes
from .google import install_google
from .nvidia import install_nvidia
from .openai import install_openai
from .openrouter import install_openrouter


def install(ctx):
    install_anthropic(ctx)
    install_chutes(ctx)
    install_google(ctx)
    install_openai(ctx)
    install_openrouter(ctx)
    install_nvidia(ctx)


__install__ = install
