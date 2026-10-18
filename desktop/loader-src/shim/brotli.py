"""Stand-in for the `brotli` module (ComfyUI's Python has fontTools but not brotli, which WOFF2 needs). Node's own zlib
does the work. Build helper for fonts.py only."""
import shutil
import subprocess

NODE = shutil.which("node") or "node"

_DEC = ("const z=require('zlib');const c=[];process.stdin.on('data',d=>c.push(d)).on('end',()=>"
        "process.stdout.write(z.brotliDecompressSync(Buffer.concat(c))))")
_ENC = ("const z=require('zlib');const c=[];process.stdin.on('data',d=>c.push(d)).on('end',()=>"
        "process.stdout.write(z.brotliCompressSync(Buffer.concat(c),{params:{[z.constants.BROTLI_PARAM_QUALITY]:11,"
        "[z.constants.BROTLI_PARAM_MODE]:z.constants.BROTLI_MODE_FONT}})))")

MODE_GENERIC, MODE_TEXT, MODE_FONT = 0, 1, 2


def _run(script, data):
    return subprocess.run([NODE, "-e", script], input=bytes(data), capture_output=True, check=True).stdout


def decompress(data):
    return _run(_DEC, data)


def compress(data, mode=MODE_GENERIC, quality=11, lgwin=22, lgblock=0):
    return _run(_ENC, data)


class error(Exception):
    pass
