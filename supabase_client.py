"""
Cliente único de Supabase, compartido por todos los blueprints.
"""
import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise RuntimeError(
        "Faltan SUPABASE_URL o SUPABASE_ANON_KEY. "
        "Copia .env.example como .env y completa tus credenciales."
    )

sb: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def sb_como_usuario(access_token: str, refresh_token: str) -> Client:
    """
    Devuelve un cliente de Supabase autenticado como el usuario de la sesión,
    para que las políticas RLS (auth.uid()) se apliquen correctamente.
    """
    cliente = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    cliente.auth.set_session(access_token, refresh_token)
    return cliente
