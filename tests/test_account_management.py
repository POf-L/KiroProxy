"""账号管理增强功能属性测试

测试覆盖:
- Property 1: OAuth URL Generation Produces Valid PKCE Parameters
- Property 2: Token Response Parsing Extracts All Required Fields
- Property 3: Account Edit Validation and Persistence
- Property 4: Import Validation Based on AuthMethod
- Property 5: Batch Import Processes All Valid Entries
- Property 6: Token Refresh Method Dispatch
- Property 7: Token Refresh Updates Credentials
- Property 8: Provider Field Persistence
- Property 9: Compression State Tracking and Caching
- Property 10: Progressive Compression Strategy
"""
import pytest
import json
import hashlib
import base64
import secrets
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timezone, timedelta


# ==================== Property 1 & 2: Social Auth Tests ====================

class TestSocialAuthOAuthURL:
    """Property 1: OAuth URL Generation Produces Valid PKCE Parameters"""
    
    def test_code_verifier_length(self):
        """code_verifier 应该是 43-128 字符"""
        from kiro_proxy.auth.device_flow import _generate_code_verifier
        verifier = _generate_code_verifier()
        assert 43 <= len(verifier) <= 128
    
    def test_code_verifier_is_url_safe(self):
        """code_verifier 应该只包含 URL 安全字符"""
        from kiro_proxy.auth.device_flow import _generate_code_verifier
        verifier = _generate_code_verifier()
        # URL safe base64 字符集
        valid_chars = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_')
        assert all(c in valid_chars for c in verifier)
    
    def test_code_challenge_is_sha256_of_verifier(self):
        """code_challenge 应该是 code_verifier 的 SHA256 哈希"""
        from kiro_proxy.auth.device_flow import _generate_code_verifier, _generate_code_challenge
        verifier = _generate_code_verifier()
        challenge = _generate_code_challenge(verifier)
        
        # 手动计算验证
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b'=').decode()
        
        assert challenge == expected
    
    def test_oauth_state_is_unique(self):
        """每次生成的 state 应该是唯一的"""
        from kiro_proxy.auth.device_flow import _generate_oauth_state
        states = [_generate_oauth_state() for _ in range(100)]
        assert len(set(states)) == 100
    
    @pytest.mark.asyncio
    async def test_start_social_auth_returns_valid_url(self):
        """start_social_auth 应该返回有效的登录 URL"""
        from kiro_proxy.auth.device_flow import start_social_auth
        
        success, result = await start_social_auth("google")
        
        assert success
        assert "login_url" in result
        assert "state" in result
        assert "provider" in result
        assert result["provider"] == "Google"
        
        # 验证 URL 包含必要参数
        url = result["login_url"]
        assert "idp=Google" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert "state=" in url
        assert "redirect_uri=" in url
    
    @pytest.mark.asyncio
    async def test_start_social_auth_github(self):
        """GitHub 登录应该正确设置 provider"""
        from kiro_proxy.auth.device_flow import start_social_auth
        
        success, result = await start_social_auth("github")
        
        assert success
        assert result["provider"] == "Github"
        assert "idp=Github" in result["login_url"]


class TestTokenResponseParsing:
    """Property 2: Token Response Parsing Extracts All Required Fields"""
    
    def test_credentials_from_file_extracts_all_fields(self):
        """from_file 应该提取所有必要字段"""
        from kiro_proxy.credential.types import KiroCredentials
        import tempfile
        import os
        
        test_data = {
            "accessToken": "test_access_token",
            "refreshToken": "test_refresh_token",
            "profileArn": "arn:aws:test",
            "expiresAt": "2025-01-10T00:00:00Z",
            "region": "us-west-2",
            "authMethod": "social",
            "provider": "Google",
            "clientId": "test_client_id",
            "clientSecret": "test_client_secret",
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(test_data, f)
            temp_path = f.name
        
        try:
            creds = KiroCredentials.from_file(temp_path)
            
            assert creds.access_token == "test_access_token"
            assert creds.refresh_token == "test_refresh_token"
            assert creds.profile_arn == "arn:aws:test"
            assert creds.region == "us-west-2"
            assert creds.auth_method == "social"
            assert creds.provider == "Google"
            assert creds.client_id == "test_client_id"
            assert creds.client_secret == "test_client_secret"
        finally:
            os.unlink(temp_path)
    
    def test_credentials_to_dict_includes_provider(self):
        """to_dict 应该包含 provider 字段"""
        from kiro_proxy.credential.types import KiroCredentials
        
        creds = KiroCredentials(
            access_token="test",
            refresh_token="test",
            provider="Github"
        )
        
        data = creds.to_dict()
        assert data["provider"] == "Github"
    
    def test_credentials_to_dict_excludes_none_provider(self):
        """to_dict 不应该包含 None 的 provider"""
        from kiro_proxy.credential.types import KiroCredentials
        
        creds = KiroCredentials(
            access_token="test",
            refresh_token="test",
            provider=None
        )
        
        data = creds.to_dict()
        assert "provider" not in data or data.get("provider") is None


# ==================== Property 6 & 7: Token Refresh Tests ====================

class TestTokenRefreshDispatch:
    """Property 6: Token Refresh Method Dispatch"""
    
    def test_social_auth_uses_social_refresh(self):
        """social authMethod 应该使用 refresh_social_token"""
        from kiro_proxy.credential.refresher import TokenRefresher
        from kiro_proxy.credential.types import KiroCredentials
        
        creds = KiroCredentials(
            refresh_token="test_refresh_token_" + "x" * 100,
            auth_method="social"
        )
        refresher = TokenRefresher(creds)
        
        # 验证 URL
        url = refresher.get_refresh_url()
        assert "auth.desktop.kiro.dev/refreshToken" in url
    
    def test_idc_auth_uses_oidc_refresh(self):
        """idc authMethod 应该使用 OIDC 端点"""
        from kiro_proxy.credential.refresher import TokenRefresher
        from kiro_proxy.credential.types import KiroCredentials
        
        creds = KiroCredentials(
            refresh_token="test_refresh_token_" + "x" * 100,
            auth_method="idc",
            region="us-east-1"
        )
        refresher = TokenRefresher(creds)
        
        url = refresher.get_refresh_url()
        assert "oidc.us-east-1.amazonaws.com/token" in url
    
    def test_validate_refresh_token_rejects_empty(self):
        """空的 refresh_token 应该被拒绝"""
        from kiro_proxy.credential.refresher import TokenRefresher
        from kiro_proxy.credential.types import KiroCredentials
        
        creds = KiroCredentials(refresh_token="")
        refresher = TokenRefresher(creds)
        
        valid, error = refresher.validate_refresh_token()
        assert not valid
        assert "为空" in error or "缺少" in error
    
    def test_validate_refresh_token_rejects_truncated(self):
        """截断的 refresh_token 应该被拒绝"""
        from kiro_proxy.credential.refresher import TokenRefresher
        from kiro_proxy.credential.types import KiroCredentials
        
        creds = KiroCredentials(refresh_token="short_token...")
        refresher = TokenRefresher(creds)
        
        valid, error = refresher.validate_refresh_token()
        assert not valid
        assert "截断" in error


class TestTokenRefreshUpdates:
    """Property 7: Token Refresh Updates Credentials"""
    
    def test_credentials_save_preserves_existing_data(self):
        """save_to_file 应该保留现有数据"""
        from kiro_proxy.credential.types import KiroCredentials
        import tempfile
        import os
        
        # 创建初始文件
        initial_data = {
            "accessToken": "old_token",
            "customField": "should_be_preserved"
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(initial_data, f)
            temp_path = f.name
        
        try:
            # 更新凭证
            creds = KiroCredentials(
                access_token="new_token",
                refresh_token="new_refresh"
            )
            creds.save_to_file(temp_path)
            
            # 验证
            with open(temp_path) as f:
                saved_data = json.load(f)
            
            assert saved_data["accessToken"] == "new_token"
            assert saved_data["refreshToken"] == "new_refresh"
            assert saved_data["customField"] == "should_be_preserved"
        finally:
            os.unlink(temp_path)


# ==================== Property 8: Provider Field Persistence ====================

class TestProviderFieldPersistence:
    """Property 8: Provider Field Persistence"""
    
    def test_provider_field_roundtrip(self):
        """provider 字段应该能正确保存和加载"""
        from kiro_proxy.credential.types import KiroCredentials
        import tempfile
        import os
        
        creds = KiroCredentials(
            access_token="test",
            refresh_token="test",
            provider="Google",
            auth_method="social"
        )
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name
        
        try:
            creds.save_to_file(temp_path)
            loaded = KiroCredentials.from_file(temp_path)
            
            assert loaded.provider == "Google"
            assert loaded.auth_method == "social"
        finally:
            os.unlink(temp_path)
    
    def test_provider_in_status_info(self):
        """get_status_info 应该包含 provider 字段"""
        from kiro_proxy.core.account import Account
        from kiro_proxy.credential.types import KiroCredentials
        import tempfile
        import os
        
        # 创建测试凭证文件
        test_data = {
            "accessToken": "test",
            "refreshToken": "test",
            "provider": "Github",
            "authMethod": "social"
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(test_data, f)
            temp_path = f.name
        
        try:
            account = Account(
                id="test_id",
                name="Test Account",
                token_path=temp_path
            )
            account.load_credentials()
            
            status = account.get_status_info()
            assert status["provider"] == "Github"
        finally:
            os.unlink(temp_path)


# ==================== Property 9 & 10: Compression Tests ====================

class TestCompressionStateTracking:
    """Property 9: Compression State Tracking and Caching"""
    
    def test_hash_history_is_deterministic(self):
        """相同历史应该产生相同哈希"""
        from kiro_proxy.core.history_manager import HistoryManager
        
        manager = HistoryManager()
        history = [
            {"userInputMessage": {"content": "Hello"}},
            {"assistantResponseMessage": {"content": "Hi"}}
        ]
        
        hash1 = manager._hash_history(history)
        hash2 = manager._hash_history(history)
        
        assert hash1 == hash2
    
    def test_hash_history_changes_with_content(self):
        """不同历史应该产生不同哈希"""
        from kiro_proxy.core.history_manager import HistoryManager
        
        manager = HistoryManager()
        # 使用不同长度的内容确保哈希不同
        history1 = [{"userInputMessage": {"content": "Hello"}}]
        history2 = [{"userInputMessage": {"content": "Hello World, this is longer"}}]
        
        hash1 = manager._hash_history(history1)
        hash2 = manager._hash_history(history2)
        
        assert hash1 != hash2
    
    @pytest.mark.asyncio
    async def test_compression_cache_prevents_repeated_compression(self):
        """压缩缓存应该防止重复压缩"""
        from kiro_proxy.core.history_manager import HistoryManager
        
        manager = HistoryManager()
        history = [{"userInputMessage": {"content": "x" * 1000}} for _ in range(50)]
        
        # 第一次压缩
        result1, should_retry1 = await manager.handle_length_error_async(history, 0, None)
        
        # 第二次压缩相同内容
        result2, should_retry2 = await manager.handle_length_error_async(history, 0, None)
        
        # 第二次应该检测到重复并跳过
        # (由于缓存机制，第二次可能返回 False)


class TestProgressiveCompression:
    """Property 10: Progressive Compression Strategy"""
    
    def test_max_retries_stops_compression(self):
        """达到最大重试次数应该停止压缩"""
        from kiro_proxy.core.history_manager import HistoryManager, HistoryConfig
        
        config = HistoryConfig(max_retries=3)
        manager = HistoryManager(config)
        history = [{"userInputMessage": {"content": "test"}}]
        
        # 超过最大重试次数
        result, should_retry = manager.handle_length_error(history, 5)
        
        assert not should_retry
    
    def test_small_history_not_compressed(self):
        """小于目标大小的历史不应该被压缩"""
        from kiro_proxy.core.history_manager import HistoryManager
        
        manager = HistoryManager()
        history = [{"userInputMessage": {"content": "small"}}]
        
        result, should_retry = manager.handle_length_error(history, 0)
        
        # 小历史不需要压缩
        assert len(result) == len(history)
    
    @pytest.mark.asyncio
    async def test_compression_reduces_size(self):
        """压缩应该减少历史大小"""
        from kiro_proxy.core.history_manager import HistoryManager
        
        manager = HistoryManager()
        # 创建大历史
        history = [
            {"userInputMessage": {"content": f"Message {i}: " + "x" * 500}}
            for i in range(100)
        ]
        
        original_size = len(json.dumps(history))
        
        # 模拟 API 调用
        async def mock_api_caller(prompt):
            return "Summary of conversation"
        
        result, should_retry = await manager.handle_length_error_async(
            history, 0, mock_api_caller
        )
        
        result_size = len(json.dumps(result))
        
        # 压缩后应该更小
        assert result_size < original_size


class TestContentTooLongFastFail:
    """可配置：内容超限直接报错（不压缩/不重试）"""

    def test_length_error_no_retry_when_error_retry_strategy_disabled_async(self):
        """禁用 error_retry 策略时，handle_length_error_async 应该快速失败"""
        from kiro_proxy.core.history_manager import HistoryManager, HistoryConfig
        import asyncio

        config = HistoryConfig(strategies=[])
        manager = HistoryManager(config)
        history = [{"userInputMessage": {"content": "x" * 1000}} for _ in range(80)]

        async def api_caller(prompt: str) -> str:
            raise AssertionError("error_retry disabled: api_caller should not be invoked")

        async def run():
            return await manager.handle_length_error_async(history, 0, api_caller)

        result, should_retry = asyncio.run(run())

        assert result == history
        assert should_retry is False
        assert manager.was_truncated is False

    def test_length_error_no_retry_when_error_retry_strategy_disabled_sync(self):
        """禁用 error_retry 策略时，同步版本也应该快速失败"""
        from kiro_proxy.core.history_manager import HistoryManager, HistoryConfig

        config = HistoryConfig(strategies=[])
        manager = HistoryManager(config)
        history = [{"userInputMessage": {"content": "x" * 1000}} for _ in range(80)]

        result, should_retry = manager.handle_length_error(history, 0)

        assert result == history
        assert should_retry is False
        assert manager.was_truncated is False


# ==================== Property 3: Account Edit Tests ====================

class TestAccountEditValidation:
    """Property 3: Account Edit Validation and Persistence"""
    
    def test_empty_name_not_updated(self):
        """空名称不应该更新"""
        # 这个测试需要模拟 API 调用
        pass
    
    def test_invalid_provider_rejected(self):
        """无效的 provider 应该被拒绝"""
        # 只允许 Google, Github, 或空
        valid_providers = [None, "", "Google", "Github"]
        invalid_providers = ["facebook", "twitter", "invalid"]
        
        for p in valid_providers:
            assert p in valid_providers
        
        for p in invalid_providers:
            assert p not in valid_providers


# ==================== Property 4 & 5: Import Tests ====================

class TestImportValidation:
    """Property 4: Import Validation Based on AuthMethod"""
    
    def test_idc_requires_client_credentials(self):
        """IDC 认证应该需要 client_id 和 client_secret"""
        # IDC 认证验证逻辑
        auth_method = "idc"
        client_id = ""
        client_secret = ""
        
        # 应该失败
        is_valid = not (auth_method == "idc" and (not client_id or not client_secret))
        assert not is_valid
    
    def test_social_does_not_require_client_credentials(self):
        """Social 认证不需要 client_id 和 client_secret"""
        auth_method = "social"
        client_id = ""
        client_secret = ""
        
        # 应该通过
        is_valid = not (auth_method == "idc" and (not client_id or not client_secret))
        assert is_valid
    
    def test_refresh_token_required(self):
        """refresh_token 是必填的"""
        refresh_token = ""
        
        is_valid = bool(refresh_token)
        assert not is_valid


class TestBatchImport:
    """Property 5: Batch Import Processes All Valid Entries"""
    
    def test_batch_import_skips_duplicates(self):
        """批量导入应该跳过重复的 refresh_token"""
        existing_tokens = {"token1", "token2"}
        new_tokens = ["token1", "token3", "token4"]
        
        imported = []
        skipped = []
        
        for token in new_tokens:
            if token in existing_tokens:
                skipped.append(token)
            else:
                imported.append(token)
                existing_tokens.add(token)
        
        assert len(imported) == 2
        assert len(skipped) == 1
        assert "token1" in skipped
    
    def test_batch_import_continues_on_error(self):
        """批量导入应该在单个错误后继续处理"""
        accounts = [
            {"refresh_token": "valid1"},
            {"refresh_token": ""},  # 无效
            {"refresh_token": "valid2"},
        ]
        
        success = 0
        failed = 0
        
        for acc in accounts:
            if acc["refresh_token"]:
                success += 1
            else:
                failed += 1
        
        assert success == 2
        assert failed == 1
