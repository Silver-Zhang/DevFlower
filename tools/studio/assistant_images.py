"""Explicitly selected project images; no path/URL fetch and no implicit history replay."""
import base64
import binascii
import struct
import zlib
from orchestration.common import canonical, digest, Blocked
from .graph import Invalid

MAX_BYTES = 2 * 1024 * 1024


def inspect(raw):
    if not raw or len(raw) > MAX_BYTES: raise Invalid('图片需小于 2MB；请缩小流程图后重试')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        offset=8; size=None; end=False; pixels=False
        while offset+12 <= len(raw):
            length=struct.unpack('>I',raw[offset:offset+4])[0];kind=raw[offset+4:offset+8]
            if length>MAX_BYTES or offset+length+12>len(raw): raise Invalid('PNG 图片不完整')
            data=raw[offset+8:offset+8+length]
            if zlib.crc32(kind+data)&0xffffffff != struct.unpack('>I',raw[offset+8+length:offset+12+length])[0]: raise Invalid('PNG 校验失败')
            if offset==8:
                if kind!=b'IHDR' or length!=13:raise Invalid('PNG 头无效')
                size=struct.unpack('>II',data[:8])
            if kind==b'IDAT':pixels=True
            offset+=length+12
            if kind==b'IEND':end=length==0 and offset==len(raw);break
        if not end or not pixels or not size:raise Invalid('PNG 图片不完整')
        mime='image/png'
    elif raw.startswith(b'\xff\xd8') and raw.endswith(b'\xff\xd9'):
        pos=2;size=None
        while pos+4<len(raw):
            if raw[pos]!=255:break
            while pos<len(raw) and raw[pos]==255:pos+=1
            marker=raw[pos];pos+=1
            if marker in (0xd9,0xda):break
            if marker in range(0xd0,0xd8) or marker==1:continue
            length=int.from_bytes(raw[pos:pos+2],'big')
            if length<2 or pos+length>len(raw):raise Invalid('JPEG 图片不完整')
            if marker in (0xc0,0xc1,0xc2):
                if length<8:raise Invalid('JPEG 头无效')
                height,width=struct.unpack('>HH',raw[pos+3:pos+7]);size=(width,height)
            pos+=length
        if not size:raise Invalid('仅支持标准 PNG / JPEG 流程图')
        mime='image/jpeg'
    else:raise Invalid('请选择 PNG 或 JPEG 图片；不接受 SVG、链接或其他文件')
    if not all(0<n<=10000 for n in size) or size[0]*size[1]>20_000_000:raise Invalid('图片尺寸过大，请缩小流程图')
    return dict(mime=mime,width=size[0],height=size[1],bytes=len(raw),sha256=digest(raw))


class AssistantImages:
    def __init__(self,rt):self.rt=rt
    def upload(self,data):
        if not isinstance(data,dict) or set(data)!={'workspace','name','data'}:raise Invalid('图片上传字段无效')
        if not isinstance(data['name'],str) or not 1<=len(data['name'])<=120 or any(c in data['name'] for c in '/\\\x00'):raise Invalid('图片名称无效')
        if not isinstance(data['data'],str) or len(data['data'])>MAX_BYTES*4//3+4:raise Invalid('图片超过上传预算')
        try:raw=base64.b64decode(data['data'],validate=True)
        except (ValueError,binascii.Error):raise Invalid('图片编码无效') from None
        meta=inspect(raw);meta.update(id=meta['sha256'],name=data['name'])
        with self.rt.project_files(data['workspace']).directory() as fd:
            self.rt.project_files(data['workspace']).write(fd,'image-'+meta['id']+'.json',dict(meta=meta,data=data['data']))
        return meta
    def resolve(self,workspace,ids):
        if not isinstance(ids,list) or len(ids)>3 or any(not isinstance(v,str) for v in ids) or len(set(ids))!=len(ids):raise Invalid('每轮最多选择三张不同图片')
        result=[]
        for key in ids:
            if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):raise Invalid('图片身份无效')
            try:
                with self.rt.project_files(workspace).directory() as fd:value=self.rt.project_files(workspace).read(fd,'image-'+key+'.json')
                raw=base64.b64decode(value['data'],validate=True);meta=inspect(raw)
                if any(meta[k]!=value['meta'][k] for k in meta) or meta['sha256']!=key or value['meta']['id']!=key:raise ValueError()
            except (OSError,ValueError,KeyError,TypeError):raise Blocked('图片不存在、已损坏或不属于当前工作区；请重新上传') from None
            result.append(value)
        return result
