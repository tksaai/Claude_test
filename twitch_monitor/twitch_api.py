import time

import aiohttp

TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX_BASE = "https://api.twitch.tv/helix"


class HelixClient:
    """Twitch Helix APIクライアント(Client Credentialsフロー、無料)。"""

    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        session = await self._get_session()
        async with session.post(
            TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
        ) as resp:
            resp.raise_for_status()
            body = await resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + body.get("expires_in", 3600)
        return self._token

    async def _api_get(self, path: str, params: list[tuple[str, str]], _retry: bool = True) -> list[dict]:
        token = await self._get_token()
        session = await self._get_session()
        async with session.get(
            f"{HELIX_BASE}/{path}",
            params=params,
            headers={
                "Client-Id": self._client_id,
                "Authorization": f"Bearer {token}",
            },
        ) as resp:
            if resp.status == 401 and _retry:
                # トークン失効: 再取得して1回だけリトライ
                self._token = None
                return await self._api_get(path, params, _retry=False)
            resp.raise_for_status()
            body = await resp.json()
        return body.get("data", [])

    async def get_streams(self, logins: list[str]) -> list[dict]:
        """指定チャンネルのうちライブ中のものを返す。"""
        if not logins:
            return []
        return await self._api_get("streams", [("user_login", login) for login in logins[:100]])

    async def get_users(self, logins: list[str]) -> list[dict]:
        if not logins:
            return []
        return await self._api_get("users", [("login", login) for login in logins[:100]])

    async def get_videos(self, user_id: str, first: int = 50) -> list[dict]:
        """指定ユーザーのアーカイブ(VOD)一覧を新しい順に返す。"""
        return await self._api_get(
            "videos",
            [("user_id", user_id), ("type", "archive"), ("first", str(first))],
        )
