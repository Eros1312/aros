"""Synthetic model names are test fixtures, never a provider recommendation."""
import os

os.environ['OPENCLAW_MODEL'] = 'test/model'
os.environ['OPENCLAW_DESIGN_MODEL'] = 'test/design-model'
os.environ['OPENAI_MODEL'] = 'test-model'
os.environ['OPENAI_API_KEY'] = ''
os.environ['OPENCLAW_URL'] = ''
os.environ['OPENCLAW_TOKEN'] = ''
os.environ['FREELLM_URL'] = ''
os.environ['FREELLM_TOKEN'] = ''
os.environ['APP_ENV'] = 'development'
