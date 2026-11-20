"""Private, normalized raster uploads shared by travel and belongings."""
import base64, binascii, hashlib, io, os, re, stat, tempfile, warnings
from pathlib import Path
import tenant_storage
from PIL import Image, ImageOps, UnidentifiedImageError

BASE = tenant_storage.path('private-images')
KINDS = {'travel': '/travel-assets/', 'treasures': '/treasure-assets/', 'receipts': '/receipt-assets/', 'people': '/people-assets/'}
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_EDGE = 2400
ID = re.compile(r'[0-9a-f]{64}\.webp')

def valid_kind(kind):
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError('图片分类无效')

def folder(kind):
    valid_kind(kind)
    target = BASE / kind
    if BASE.is_symlink() or target.is_symlink(): raise ValueError('图片目录无效')
    if (BASE.exists() and not BASE.is_dir()) or (target.exists() and not target.is_dir()):
        raise ValueError('图片目录无效')
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    BASE.chmod(0o700); target.chmod(0o700)
    return target

def resolve(kind, key):
    valid_kind(kind)
    if not isinstance(key, str) or not ID.fullmatch(key): raise ValueError('图片编号无效')
    path = BASE / kind / key
    if BASE.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_file(): raise ValueError('图片不存在')
    return path

def validate_ids(kind, ids):
    valid_kind(kind)
    if not isinstance(ids, list) or len(ids)>8: raise ValueError('最多添加 8 张照片')
    result=[]
    for key in ids:
        resolve(kind, key)
        if key not in result: result.append(key)
    return result

def store(kind, data_url):
    valid_kind(kind)
    if not isinstance(data_url,str) or len(data_url)>MAX_BYTES*4//3+100: raise ValueError('单张照片请小于 8 MB')
    match=re.fullmatch(r'data:image/(jpeg|png|webp|gif);base64,([A-Za-z0-9+/=]+)',data_url)
    if not match: raise ValueError('请选择 JPG、PNG、WebP 或 GIF 图片')
    try: raw=base64.b64decode(match[2],validate=True)
    except (ValueError,binascii.Error): raise ValueError('图片数据无效') from None
    if not raw or len(raw)>MAX_BYTES: raise ValueError('单张照片请小于 8 MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            # Verify the real format and container before decoding pixel data.
            with Image.open(io.BytesIO(raw)) as source:
                if source.format != {'jpeg':'JPEG','png':'PNG','webp':'WEBP','gif':'GIF'}[match[1]]:
                    raise ValueError('图片内容与格式不一致')
                if source.width*source.height>MAX_PIXELS: raise ValueError('图片尺寸过大或格式无效')
                source.verify()
            with Image.open(io.BytesIO(raw)) as source:
                source.seek(0)
                oriented=ImageOps.exif_transpose(source)
                alpha='A' in oriented.getbands() or 'transparency' in oriented.info
                im=oriented.convert('RGBA' if alpha else 'RGB')
                im.thumbnail((MAX_EDGE,MAX_EDGE),Image.Resampling.LANCZOS)
                # Rebuild from pixels: EXIF, GPS, XMP, ICC and textual comments
                # must not survive Pillow's usual image.info inheritance.
                clean=Image.new(im.mode,im.size)
                clean.paste(im)
                out=io.BytesIO(); clean.save(out,'WEBP',quality=88,method=4,
                                          exif=b'',icc_profile=b'',xmp=b'')
                width,height=clean.size
    except (UnidentifiedImageError,OSError,SyntaxError,EOFError,Image.DecompressionBombError,Image.DecompressionBombWarning): raise ValueError('无法读取这张照片，请换一张图片') from None
    content=out.getvalue(); key=hashlib.sha256(content).hexdigest()+'.webp'; target=folder(kind)/key
    if target.is_symlink(): raise ValueError('图片保存校验失败')
    if target.exists():
        if not target.is_file(): raise ValueError('图片保存校验失败')
        # Never follow a replacement symlink when verifying or tightening mode.
        fd=os.open(target,os.O_RDONLY | getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as existing:
            details=os.fstat(existing.fileno())
            if not stat.S_ISREG(details.st_mode) or details.st_size!=len(content) or existing.read()!=content:
                raise ValueError('图片保存校验失败')
            os.fchmod(existing.fileno(),0o600)
    else:
        fd,name=tempfile.mkstemp(prefix='.upload-',dir=target.parent)
        try:
            with os.fdopen(fd,'wb') as f: f.write(content); f.flush(); os.fsync(f.fileno())
            os.replace(name,target)
            # The API must not acknowledge a file before its directory entry
            # has been flushed; metadata may be saved immediately afterwards.
            directory_fd=os.open(target.parent,os.O_RDONLY | getattr(os,'O_DIRECTORY',0))
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        finally:
            if os.path.exists(name): os.unlink(name)
    return {'id':key,'url':KINDS[kind]+key,'width':width,'height':height}
