# -*- coding: utf-8 -*-
"""本机自签证书签发（手机/平板网页端录音专用）

手机浏览器只在「安全上下文」才允许 getUserMedia 开麦：
  1) https://<局域网地址>...
  2) http://127.0.0.1 / http://localhost（本机回环例外）
手机通过 http://192.168.x.x 打开页面时 navigator.mediaDevices 直接不存在，
所以必须给局域网访问提供 HTTPS。这里用 cryptography 现场签发一张自签证书，
证书 SAN 覆盖本机回环、主机名与全部本机私网 IPv4，IP 变化时自动重签。

证书与私钥存放在用户数据目录（data/certs），不写入程序安装目录。
"""
import os
import socket
import datetime
import ipaddress
import threading

_LOCK = threading.Lock()

CERT_NAME = "mobile_server.crt"
KEY_NAME = "mobile_server.key"
DAYS_VALID = 825          # 手机端浏览器对超长有效期更敏感，取 825 天（≈27 个月）
RENEW_BEFORE_DAYS = 7     # 剩余有效期不足 7 天即重签


def _private_ipv4():
    """本机私网 IPv4 列表（排除回环 / 169.254 / 虚拟网卡不排除，只要私网段即可）"""
    out = []

    def _add(ip):
        ip = str(ip or "").strip()
        if not ip or ip in out:
            return
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return
        if a.version != 4 or a.is_loopback or a.is_link_local or a.is_unspecified:
            return
        out.append(ip)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.3)
        try:
            s.connect(("223.5.5.5", 80))
            _add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(info[4][0])
    except Exception:
        pass
    return out


def cert_hosts():
    """证书需要覆盖的地址：回环 + 主机名 + 全部私网 IPv4"""
    ips = ["127.0.0.1"] + _private_ipv4()
    dns = ["localhost"]
    try:
        host = socket.gethostname()
        if host and host not in dns:
            dns.append(host)
    except Exception:
        pass
    return ips, dns


def _read_cert(path):
    from cryptography import x509
    with open(path, "rb") as f:
        return x509.load_pem_x509_certificate(f.read())


def _cert_sans(cert):
    """取出证书里的 IP / DNS 列表"""
    from cryptography import x509
    ips, dns = [], []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        ips = [str(x) for x in ext.get_values_for_type(x509.IPAddress)]
        dns = [str(x) for x in ext.get_values_for_type(x509.DNSName)]
    except Exception:
        pass
    return ips, dns


def _cert_not_after(cert):
    try:
        return cert.not_valid_after_utc.replace(tzinfo=None)
    except Exception:
        return getattr(cert, "not_valid_after", None)


def inspect(cert_dir):
    """查看现有证书状态（不生成）"""
    cert_path = os.path.join(cert_dir, CERT_NAME)
    key_path = os.path.join(cert_dir, KEY_NAME)
    info = {"ok": False, "cert": cert_path, "key": key_path, "exists": False,
            "ips": [], "dns": [], "not_after": "", "days_left": 0,
            "cover_missing": [], "error": ""}
    if not (os.path.isfile(cert_path) and os.path.isfile(key_path)):
        info["error"] = "证书文件不存在"
        return info
    try:
        cert = _read_cert(cert_path)
        ips, dns = _cert_sans(cert)
        na = _cert_not_after(cert)
        days = (na - datetime.datetime.utcnow()).days if na else 0
        need_ips, need_dns = cert_hosts()
        missing = [x for x in need_ips if x not in ips] + [x for x in need_dns if x not in dns]
        info.update({"ok": True, "exists": True, "ips": ips, "dns": dns,
                     "not_after": na.strftime("%Y-%m-%d %H:%M:%S") if na else "",
                     "days_left": days, "cover_missing": missing})
    except Exception as e:
        info["error"] = "证书读取失败：%s" % e
    return info


def ensure_cert(cert_dir, days=DAYS_VALID, force=False):
    """确保证书可用：不存在 / 即将过期 / 未覆盖当前局域网 IP 时自动重新签发

    返回 dict：ok / cert / key / created / error / ips / dns / not_after / fingerprint
    """
    os.makedirs(cert_dir, exist_ok=True)
    cert_path = os.path.join(cert_dir, CERT_NAME)
    key_path = os.path.join(cert_dir, KEY_NAME)
    with _LOCK:
        cur = inspect(cert_dir)
        reuse = (not force) and cur["ok"] and cur["days_left"] > RENEW_BEFORE_DAYS \
            and not cur["cover_missing"]
        if reuse:
            out = dict(cur)
            out["created"] = False
            out["fingerprint"] = _fingerprint(cert_path)
            return out
        try:
            _issue(cert_path, key_path, days)
        except Exception as e:
            return {"ok": False, "cert": cert_path, "key": key_path, "created": False,
                    "error": "自签证书签发失败：%s（需安装 cryptography：pip install cryptography）" % e,
                    "ips": [], "dns": [], "not_after": "", "fingerprint": ""}
        out = inspect(cert_dir)
        out["created"] = True
        out["fingerprint"] = _fingerprint(cert_path)
        return out


def _fingerprint(cert_path):
    """证书 SHA-256 指纹（页面可展示，便于用户核对手机提示的是不是这张证书）"""
    import hashlib
    from cryptography.hazmat.primitives import serialization
    try:
        der = _read_cert(cert_path).public_bytes(serialization.Encoding.DER)
        return hashlib.sha256(der).hexdigest().upper()
    except Exception:
        return ""


def _issue(cert_path, key_path, days):
    """生成 RSA 2048 自签证书并落盘（私钥权限尽量收紧）"""
    from cryptography import x509
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    ips, dns = cert_hosts()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "InterviewLiveNote Mobile"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "InterviewLiveNote"),
    ])
    san = []
    for d in dns:
        san.append(x509.DNSName(d))
    for ip in ips:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            pass
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=int(days)))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(key_path, "wb") as f:
        f.write(key_pem)
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(key_path, 0o600)
    except Exception:
        pass
    return cert_path, key_path
