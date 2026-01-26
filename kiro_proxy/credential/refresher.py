"""Token 刷新器"""
import httpx
from datetime import datetime, timezone, timedelta
from typing import Tuple

from .types import KiroCredentials
from .fingerprint import generate_machine_id, get_kiro_version


# Kiro Auth 端点
KIRO_AUTH_ENDPOINT = "https://prod.us-east-1.auth.desktop.kiro.dev"


class TokenRefresher:
    """Token 刷新器"""
    
    def __init__(self, credentials: KiroCredentials):
        self.credentials = credentials
    
    def get_refresh_url(self) -> str:
        """获取刷新 URL"""
        region = self.credentials.region or "us-east-1"
        auth_method = (self.credentials.auth_method or "social").lower()
        
        if auth_method == "idc":
            # IDC (AWS Builder ID) 使用 OIDC 端点
            return f"https://oidc.{region}.amazonaws.com/token"
        else:
            # Social (Google/GitHub) 使用 Kiro Auth 端点
            return f"{KIRO_AUTH_ENDPOINT}/refreshToken"
    
    def validate_refresh_token(self) -> Tuple[bool, str]:
        """验证 refresh_token 有效性"""
        refresh_token = self.credentials.refresh_token
        
        if not refresh_token:
            return False, "缺少 refresh_token"
        
        if len(refresh_token.strip()) == 0:
            return False, "refresh_token 为空"
        
        if len(refresh_token) < 100 or refresh_token.endswith("..."):
            return False, f"refresh_token 已被截断（长度: {len(refresh_token)}）"
        
        return True, ""
    
    def _get_machine_id(self) -> str:
        """获取 Machine ID"""
        return generate_machine_id(
            self.credentials.profile_arn, 
            self.credentials.client_id
        )
    
    async def refresh_social_token(self) -> Tuple[bool, str]:
        """
        刷新 Social Token (Google/GitHub)
        
        参考 Kiro-account-manager 实现:
        - 端点: https://prod.us-east-1.auth.desktop.kiro.dev/refreshToken
        - 请求体: {"refreshToken": refresh_token}
        - 响应: {accessToken, refreshToken, expiresIn}
        """
        refresh_url = f"{KIRO_AUTH_ENDPOINT}/refreshToken"
        
        body = {"refreshToken": self.credentials.refresh_token}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "kiro-proxy/1.0.0",
            "Accept": "application/json",
        }
        
        try:
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                resp = await client.post(refresh_url, json=body, headers=headers)
                
                if resp.status_code != 200:
                    error_text = resp.text
                    if resp.status_code == 401:
                        return False, "凭证已过期或无效，需要重新登录"
                    elif resp.status_code == 429:
                        return False, "请求过于频繁，请稍后重试"
                    else:
                        return False, f"刷新失败: {resp.status_code} - {error_text[:200]}"
                
                data = resp.json()
                
                new_token = data.get("accessToken")
                if not new_token:
                    return False, "响应中没有 accessToken"
                
                # 更新凭证
                self.credentials.access_token = new_token
                
                # 更新 refreshToken（如果服务器返回了新的）
                if rt := data.get("refreshToken"):
                    self.credentials.refresh_token = rt
                
                # 更新过期时间
                if expires_in := data.get("expiresIn"):
                    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    self.credentials.expires_at = expires_at.isoformat()
                
                self.credentials.last_refresh = datetime.now(timezone.utc).isoformat()
                
                print(f"[TokenRefresher] Social token 刷新成功，过期时间: {expires_in}s")
                return True, new_token
                
        except Exception as e:
            return False, f"刷新异常: {str(e)}"
    
    async def refresh_idc_token(self) -> Tuple[bool, str]:
        """
        刷新 IDC Token (AWS Builder ID)
        
        使用 AWS OIDC 端点刷新
        """
        region = self.credentials.region or "us-east-1"
        refresh_url = f"https://oidc.{region}.amazonaws.com/token"
        
        if not self.credentials.client_id or not self.credentials.client_secret:
            return False, "IdC 认证缺少 client_id 或 client_secret"
        
        machine_id = self._get_machine_id()
        kiro_version = get_kiro_version()
        
        body = {
            "refreshToken": self.credentials.refresh_token,
            "clientId": self.credentials.client_id,
            "clientSecret": self.credentials.client_secret,
            "grantType": "refresh_token"
        }
        headers = {
            "Content-Type": "application/json",
            "x-amz-user-agent": f"aws-sdk-js/3.738.0 KiroIDE-{kiro_version}-{machine_id}",
            "User-Agent": "node",
        }
        
        try:
            async with httpx.AsyncClient(verify=False, timeout=30) as client:
                resp = await client.post(refresh_url, json=body, headers=headers)
                
                if resp.status_code != 200:
                    error_text = resp.text
                    if resp.status_code == 401:
                        return False, "凭证已过期或无效，需要重新登录"
                    elif resp.status_code == 429:
                        return False, "请求过于频繁，请稍后重试"
                    else:
                        return False, f"刷新失败: {resp.status_code} - {error_text[:200]}"
                
                data = resp.json()
                
                new_token = data.get("accessToken") or data.get("access_token")
                if not new_token:
                    return False, "响应中没有 access_token"
                
                # 更新凭证
                self.credentials.access_token = new_token
                
                if rt := data.get("refreshToken") or data.get("refresh_token"):
                    self.credentials.refresh_token = rt
                
                if arn := data.get("profileArn"):
                    self.credentials.profile_arn = arn
                
                if expires_in := data.get("expiresIn") or data.get("expires_in"):
                    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                    self.credentials.expires_at = expires_at.isoformat()
                
                self.credentials.last_refresh = datetime.now(timezone.utc).isoformat()
                
                print(f"[TokenRefresher] IDC token 刷新成功")
                return True, new_token
                
        except Exception as e:
            return False, f"刷新异常: {str(e)}"
    
    async def refresh(self) -> Tuple[bool, str]:
        """
        刷新 token，根据 authMethod 分发到正确的刷新方法
        
        Returns:
            (success, new_token_or_error)
        """
        is_valid, error = self.validate_refresh_token()
        if not is_valid:
            return False, error
        
        auth_method = (self.credentials.auth_method or "social").lower()
        
        if auth_method == "idc":
            return await self.refresh_idc_token()
        else:
            # social 或其他默认使用 social 刷新
            return await self.refresh_social_token()
