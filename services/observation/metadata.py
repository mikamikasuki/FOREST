"""Bounded, non-executing metadata readers. Samples never imply full coverage."""
import csv
import io
import os
import json
from pathlib import PurePosixPath

SAMPLE_BYTES = 32768
FOOTER_BYTES = 262144

def inspect(fd, size, path, content):
    suffix=PurePosixPath(path).suffix.lower()
    result={'size_bytes':size, 'content_complete':content is not None}
    def read_at(offset,count):
        return os.pread(fd,min(count,size-offset),offset)
    try:
        if suffix in ('.csv','.tsv'):
            sample=content if content is not None else read_at(0,SAMPLE_BYTES)
            # A prefix ending within a UTF-8 character is explicitly a sample.
            text=sample.decode('utf-8',errors='ignore')
            rows=[]; reader=csv.reader(io.StringIO(text,newline=''),delimiter='\t' if suffix=='.tsv' else ',')
            for row in reader:
                rows.append([v[:240] for v in row[:100]])
                if len(rows)==11: break
            result.update(format='delimited',columns=rows[0] if rows else [],sample_rows=rows[1:],
                          sample_bytes=len(sample),sample_only=True,total_rows=None)
        elif suffix=='.parquet':
            tail=read_at(max(0,size-8),8)
            if len(tail)!=8 or tail[4:]!=b'PAR1' or read_at(0,4)!=b'PAR1': raise ValueError()
            length=int.from_bytes(tail[:4],'little')
            if length>FOOTER_BYTES or length>size-12:
                result.update(format='parquet',coverage='footer exceeds metadata bound'); return result
            footer=read_at(size-8-length,length)
            import pyarrow as pa
            import pyarrow.parquet as pq
            meta=pq.ParquetFile(pa.BufferReader(b'PAR1'+footer+tail),thrift_string_size_limit=FOOTER_BYTES,
                                thrift_container_size_limit=10000,arrow_extensions_enabled=False).metadata
            result.update(format='parquet',rows=meta.num_rows,row_groups=meta.num_row_groups,
                          columns=[{'name':meta.schema.column(i).name[:240],'physical_type':meta.schema.column(i).physical_type}
                                   for i in range(min(meta.num_columns,100))],
                          columns_truncated=meta.num_columns>100,sample_rows=[],coverage='footer metadata only; no rows read')
        elif suffix in ('.png','.jpg','.jpeg','.gif','.webp'):
            from PIL import Image
            # Header parsing only; no raster decode or load().
            with Image.open(io.BytesIO(read_at(0,FOOTER_BYTES))) as im:
                result.update(format=im.format,width=im.width,height=im.height,coverage='bounded image header only')
        elif suffix=='.pdf' and content is not None:
            from pypdf import PdfReader
            document=PdfReader(io.BytesIO(content))
            result.update(format='pdf',pages=len(document.pages),coverage='page metadata; no OCR or text extraction')
        elif suffix in ('.pt','.pth','.ckpt','.pkl','.pickle','.bin'):
            result.update(format='binary',coverage='size and file identity only; not deserialized')
    except Exception:
        result.update(coverage='metadata unavailable or invalid; no content claims')
    for field in ('sample_rows','columns'):
        while result.get(field) and len(json.dumps(result,ensure_ascii=False).encode())>8192:
            result[field].pop(); result['metadata_fields_truncated']=True
    return result
