def install(ctx):
    ctx.add_importmaps({"katex": f"{ctx.ext_prefix}/katex.min.mjs"})
    ctx.add_index_footer(f"""<link rel="stylesheet" href="{ctx.ext_prefix}/katex.min.css">""")


__install__ = install
