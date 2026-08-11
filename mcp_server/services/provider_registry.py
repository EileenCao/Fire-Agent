"""Explicit provider selection for instrument research."""


class ProviderRegistry:
    def __init__(self, providers=None):
        self._providers = dict(providers or {})

    def register(self, provider_id, provider):
        key = str(provider_id).strip()
        if not key:
            raise ValueError("provider_id 不能为空")
        self._providers[key] = provider

    def get(self, provider_id="a-stock-data"):
        key = str(provider_id or "a-stock-data").strip()
        if key not in self._providers:
            raise ValueError(
                "未知 provider_id：{}；可用 Provider：{}".format(
                    key, ", ".join(self.ids()) or "无"
                )
            )
        return self._providers[key]

    def ids(self):
        return sorted(self._providers)
