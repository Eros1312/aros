from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    app_env: str = 'development'
    demo_mode: bool = True
    database_url: str = 'sqlite:///./aros.db'
    public_origin: str = 'http://127.0.0.1:8000'
    admin_email: str = 'aros@local'
    admin_password: str = ''
    encryption_key: str = ''
    google_client_id: str = ''
    google_client_secret: str = ''
    openai_api_key: str = ''
    openai_model: str = ''
    openclaw_url: str = ''
    openclaw_token: str = ''
    openclaw_model: str = ''
    openclaw_design_model: str = ''
    freellm_url: str = ''
    freellm_token: str = ''
    freellm_models: str = ''
    server_agent_url: str = ''
    server_agent_token: str = ''
    rss_urls: str = ''
    web_dir: str = '../web/out'
    session_days: int = 7
    @property
    def default_model(self):
        return self.openclaw_model or ('openai/'+self.openai_model if self.openai_model else 'local')

    @property
    def agent_configured(self):
        return bool(self.openclaw_url and self.openclaw_token and self.openclaw_model)

    def validate_production(self):
        if self.openai_api_key and not self.openai_model:
            raise RuntimeError('Set OPENAI_MODEL explicitly before using OPENAI_API_KEY')
        if self.openclaw_url and not self.agent_configured:
            raise RuntimeError('OpenClaw requires OPENCLAW_URL, OPENCLAW_TOKEN and explicit OPENCLAW_MODEL')
        if self.openai_api_key and self.openclaw_url:
            raise RuntimeError('Configure one AI transport at a time; Aros never chooses a provider silently')
        if self.app_env == 'production':
            if self.demo_mode or len(self.admin_password) < 16 or not self.encryption_key or not self.public_origin.startswith('https://'):
                raise RuntimeError('Production requires DEMO_MODE=false, ADMIN_PASSWORD (16+ characters), ENCRYPTION_KEY and HTTPS PUBLIC_ORIGIN')
            from cryptography.fernet import Fernet
            try: Fernet(self.encryption_key.encode())
            except (ValueError, TypeError): raise RuntimeError('ENCRYPTION_KEY must be a valid Fernet key') from None
settings = Settings()
