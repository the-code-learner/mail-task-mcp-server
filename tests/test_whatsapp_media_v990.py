from __future__ import annotations

import base64
import json
import unittest

import httpx

from postmaster.whatsapp_v990.binary import BinaryNode
from postmaster.whatsapp_v990.media import (
    MediaConn,
    MediaHost,
    MediaUpload,
    build_media_conn_query,
    decode_media_descriptor,
    download_media,
    encode_media_message,
    infer_media_type,
    parse_media_conn,
    upload_media_bytes,
    upload_token,
)


class WhatsAppMediaV990Tests(unittest.IsolatedAsyncioTestCase):
    def test_media_conn_query_and_parse(self):
        query=build_media_conn_query()
        self.assertEqual(query.tag,"iq")
        self.assertEqual(query.attrs["xmlns"],"w:m")
        self.assertEqual(query.child("media_conn").tag,"media_conn")
        result=BinaryNode("iq",{"type":"result"},[
            BinaryNode("media_conn",{"auth":"token secret","ttl":"3600"},[
                BinaryNode("host",{"hostname":"mmg.example.test","maxContentLengthBytes":"1000000"})
            ])
        ])
        conn=parse_media_conn(result)
        self.assertEqual(conn.auth,"token secret")
        self.assertEqual(conn.ttl,3600)
        self.assertEqual(conn.hosts[0].hostname,"mmg.example.test")
        self.assertEqual(conn.hosts[0].max_content_length_bytes,1000000)

    def test_infer_type_and_upload_token(self):
        self.assertEqual(infer_media_type(mimetype="image/jpeg",filename="x.jpg"),"image")
        self.assertEqual(infer_media_type(mimetype="image/webp",filename="x.webp"),"sticker")
        self.assertEqual(infer_media_type(mimetype="application/pdf",filename="x.pdf"),"document")
        token=upload_token(bytes(range(32)))
        self.assertNotIn("=",token)
        self.assertNotIn("+",token)
        self.assertNotIn("/",token)

    async def test_upload_encrypts_posts_and_returns_metadata(self):
        seen={}
        async def handler(request: httpx.Request):
            seen["url"]=str(request.url)
            seen["origin"]=request.headers.get("origin")
            seen["content_type"]=request.headers.get("content-type")
            seen["body"]=await request.aread()
            return httpx.Response(200,json={
                "url":"https://mmg.example.test/v/t62.7118/file",
                "direct_path":"/v/t62.7118/file",
            })
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            upload,encrypted=await upload_media_bytes(
                b"hello stored file",
                media_type="document",
                mimetype="text/plain",
                filename="note.txt",
                caption="caption",
                media_conn=MediaConn("auth token",3600,(MediaHost("mmg.example.test",100000),)),
                client=client,
            )
        finally:
            await client.aclose()
        self.assertEqual(upload.media_type,"document")
        self.assertEqual(upload.filename,"note.txt")
        self.assertEqual(upload.caption,"caption")
        self.assertEqual(upload.file_length,len(b"hello stored file"))
        self.assertEqual(seen["origin"],"https://web.whatsapp.com")
        self.assertEqual(seen["content_type"],"application/octet-stream")
        self.assertEqual(seen["body"],encrypted)
        self.assertIn("/mms/document/",seen["url"])
        self.assertIn("auth=auth%20token",seen["url"])

    async def test_media_proto_roundtrip_for_supported_types(self):
        for kind,mimetype,filename,caption in (
            ("image","image/jpeg",None,"image cap"),
            ("document","application/pdf","file.pdf","document cap"),
            ("audio","audio/ogg",None,""),
            ("video","video/mp4",None,"video cap"),
            ("sticker","image/webp","sticker.webp",""),
        ):
            upload=MediaUpload(
                media_type=kind,
                url="https://mmg.example.test/file",
                direct_path="/file",
                media_key=b"k"*32,
                file_sha256=b"s"*32,
                file_enc_sha256=b"e"*32,
                file_length=1234,
                mimetype=mimetype,
                filename=filename,
                caption=caption,
                media_key_timestamp=1790000000,
            )
            raw=encode_media_message(upload)
            decoded=decode_media_descriptor(raw)
            self.assertIsNotNone(decoded)
            self.assertEqual(decoded.media_type,kind)
            self.assertEqual(decoded.url,upload.url)
            self.assertEqual(decoded.direct_path,upload.direct_path)
            self.assertEqual(decoded.media_key,upload.media_key)
            self.assertEqual(decoded.file_sha256,upload.file_sha256)
            self.assertEqual(decoded.file_enc_sha256,upload.file_enc_sha256)
            self.assertEqual(decoded.file_length,upload.file_length)
            self.assertEqual(decoded.mimetype,mimetype)
            if kind=="document":
                self.assertEqual(decoded.filename,filename)
                self.assertEqual(decoded.caption,caption)
            elif kind in {"image","video"}:
                self.assertEqual(decoded.caption,caption)

    async def test_download_verifies_encrypted_hash_mac_plain_hash_and_length(self):
        plaintext=b"media payload"*100
        async def upload_handler(request: httpx.Request):
            body=await request.aread()
            upload_handler.encrypted=bytes(body)
            return httpx.Response(200,json={"direct_path":"/media/abc"})
        upload_handler.encrypted=b""
        upload_client=httpx.AsyncClient(transport=httpx.MockTransport(upload_handler))
        try:
            upload,_=await upload_media_bytes(
                plaintext,
                media_type="image",
                mimetype="image/jpeg",
                filename="x.jpg",
                media_conn=MediaConn("a",3600,(MediaHost("mmg.example.test",100000),)),
                client=upload_client,
            )
        finally:
            await upload_client.aclose()
        descriptor=decode_media_descriptor(encode_media_message(upload))
        async def download_handler(request: httpx.Request):
            self.assertEqual(request.headers.get("origin"),"https://web.whatsapp.com")
            return httpx.Response(200,content=upload_handler.encrypted)
        download_client=httpx.AsyncClient(transport=httpx.MockTransport(download_handler))
        try:
            restored=await download_media(descriptor,client=download_client,default_host="mmg.example.test")
        finally:
            await download_client.aclose()
        self.assertEqual(restored,plaintext)


if __name__=="__main__":
    unittest.main()
