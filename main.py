# main.py — Pharmy-C API v4.2 CORRIGÉ
# ============================================================
# CORRECTIONS APPLIQUÉES (gestion d'erreurs centralisée) :
#
#   1. Exception handlers globaux pour TOUS les types d'erreurs :
#      - HTTPException           → erreur métier (4xx/5xx)
#      - RequestValidationError  → payload invalide (422)
#      - ValidationError Pydantic → validation interne (422)
#      - SQLAlchemyError         → erreur base de données (500)
#      - Exception générique     → erreur inattendue (500)
#
#   2. Format de réponse unifié pour TOUTES les erreurs :
#      {
#        "status":  "error" | "warning" | "success",
#        "code":    <HTTP status code int>,
#        "message": <message lisible>,
#        "details": <liste de détails optionnels>
#      }
#
#   3. Warnings 4xx (403, 404, 409, 429) → status="warning"
#      Erreurs 5xx → status="error"
#
#   4. Le frontend N'a PAS besoin de construire ses propres
#      messages d'erreur. Il lit toujours response.data.message
#      et response.data.status.
#
#   5. Logs serveur conservés pour débogage.
#
#   6. Aucune fonctionnalité modifiée.
# ============================================================

import os
import uuid
import logging
import traceback
from datetime import datetime
from contextlib import asynccontextmanager

from routers import dashboard
from routers import prix_fournisseur
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import (
    SQLAlchemyError,
    IntegrityError,
    OperationalError,
    DataError,
)
from pydantic import ValidationError

from database import engine, Base, SessionLocal
from utils.security import get_password_hash
from routers import abonnements, admin as admin_router

logger = logging.getLogger("pharmy_c")

# ── Rate limiting (slowapi) ──────────────────────────────────
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    limiter = Limiter(key_func=get_remote_address)
    _rate_limit_available = True
except ImportError:
    limiter = None
    _rate_limit_available = False
    print("⚠️  slowapi non installé — pip install slowapi")

# ─────────────────────────────────────────────────────────────
# 1. Import de TOUS les modèles AVANT create_all
# ─────────────────────────────────────────────────────────────
from models.models import (
    Pharmacie, Role, Menu, RoleMenu, Utilisateur, Client,
    Produit, EntreeStock, SortieStock, Vente, DetailVente,
    Paiement, Notification, Historique, TokenBlacklist
)
from models.fournisseurs import Fournisseur, BonCommande, LigneBonCommande
from models.ordonnances  import Ordonnance, LigneOrdonnance
from models.retours      import RetourProduit, LigneRetour
from models.factures     import Facture

# Modèles messagerie (v4.2) — import conditionnel
try:
    from models.messaging import NotificationV2, Conversation, ConversationParticipant, Message
    _messaging_models_ok = True
except ImportError as e:
    _messaging_models_ok = False
    print(f"⚠️  models/messaging.py non trouvé : {e}")

# Modèles prix fournisseur (v4.3) — import conditionnel
try:
    from models.prix_fournisseur import PrixAchatFournisseur, HistoriquePrixFournisseur
    _prix_fournisseur_models_ok = True
except ImportError as e:
    _prix_fournisseur_models_ok = False
    print(f"⚠️  models/prix_fournisseur.py non trouvé : {e}")

# Modèles abonnements — import conditionnel
try:
    from models.abonnements import Abonnement, PaiementAbonnement
    _abonnement_models_ok = True
except ImportError as e:
    _abonnement_models_ok = False
    print(f"⚠️  models/abonnements.py non trouvé : {e}")


# ─────────────────────────────────────────────────────────────
# 2a. Migrations SQL (colonnes manquantes)
# ─────────────────────────────────────────────────────────────

def _migrer_colonnes(eng):
    """Ajoute les colonnes manquantes sans casser l'existant."""
    migrations = [
        # Produits
        "ALTER TABLE produits ADD COLUMN IF NOT EXISTS image_url VARCHAR(500)",
        "ALTER TABLE produits ADD COLUMN IF NOT EXISTS pieces_par_plaquette INTEGER DEFAULT 1",
        "ALTER TABLE produits ADD COLUMN IF NOT EXISTS stock_total_piece INTEGER DEFAULT 0",
        # Pharmacies
        "ALTER TABLE pharmacies ADD COLUMN IF NOT EXISTS devise VARCHAR(10) DEFAULT 'MGA'",
        "ALTER TABLE pharmacies ADD COLUMN IF NOT EXISTS owner_user_id INTEGER",
        # Utilisateurs
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS uuid VARCHAR(36)",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS telephone VARCHAR(50)",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS actif BOOLEAN DEFAULT TRUE",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS code_confirmation VARCHAR(10)",
        "ALTER TABLE utilisateurs ADD COLUMN IF NOT EXISTS confirmation_email BOOLEAN DEFAULT FALSE",
        # Ventes
        "ALTER TABLE ventes ADD COLUMN IF NOT EXISTS id_utilisateur INTEGER",
        # Entrées stock
        "ALTER TABLE entrees_stock ADD COLUMN IF NOT EXISTS prix_achat_unitaire NUMERIC(12,4)",
        "ALTER TABLE entrees_stock ADD COLUMN IF NOT EXISTS montant_achat NUMERIC(12,2)",
        "ALTER TABLE entrees_stock ADD COLUMN IF NOT EXISTS id_bon_commande_ligne INTEGER",
        "ALTER TABLE entrees_stock ADD COLUMN IF NOT EXISTS id_fournisseur INTEGER",
        # Historique
        "ALTER TABLE historique ADD COLUMN IF NOT EXISTS id_pharmacie INTEGER",
        "ALTER TABLE historique ADD COLUMN IF NOT EXISTS entity_type VARCHAR(100)",
        "ALTER TABLE historique ADD COLUMN IF NOT EXISTS entity_id INTEGER",
        "ALTER TABLE historique ADD COLUMN IF NOT EXISTS old_value TEXT",
        "ALTER TABLE historique ADD COLUMN IF NOT EXISTS new_value TEXT",
    ]

    with eng.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
                print(f"✅ Migration : {sql[:70]}...")
            except Exception as e:
                conn.rollback()
                print(f"ℹ️  Migration ignorée : {str(e)[:80]}")

_migrer_colonnes(engine)

# ─────────────────────────────────────────────────────────────
# 2b. Création des tables
# ─────────────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# ─────────────────────────────────────────────────────────────
# 3. Import des routeurs
# ─────────────────────────────────────────────────────────────
from routers import (
    auth, utilisateurs, pharmacies, produits,
    entrees_stock, sorties_stock, ventes,
    historique, roles, menus, clients,
    notifications,
    fournisseurs, bons_commande, ordonnances,
    retours, factures, rapports,
)

# ── Messagerie (v4.2) ────────────────────────────────────
try:
    from routers import messages as messages_router
    _messages_ok = True
except ImportError as e:
    _messages_ok = False
    print(f"⚠️  Router messages non trouvé : {e}")

# ─────────────────────────────────────────────────────────────
# 4. Données d'initialisation
# ─────────────────────────────────────────────────────────────

ROLES_DEFAUT = [
    {"name": "admin",              "description": "Administrateur système — accès illimité"},
    {"name": "proprietaire",       "description": "Propriétaire — gère sa pharmacie complètement"},
    {"name": "vendeur",            "description": "Vendeur — crée des ventes, gère clients et ordonnances"},
    {"name": "caissier",           "description": "Caissier — encaisse les paiements, consulte les ventes"},
    {"name": "gestionnaire_stock", "description": "Gestionnaire stock — gère produits, entrées/sorties, fournisseurs"},
]

MENUS_DEFAUT = [
    {"name": "Dashboard",      "path": "/dashboard",      "icon": "home",              "order": 1,  "is_active": True},
    {"name": "Produits",       "path": "/produits",       "icon": "cube",              "order": 2,  "is_active": True},
    {"name": "Ventes",         "path": "/ventes",         "icon": "cart",              "order": 3,  "is_active": True},
    {"name": "Commandes",      "path": "/commandes",      "icon": "clipboard",         "order": 4,  "is_active": True},
    {"name": "Clients",        "path": "/clients",        "icon": "person",            "order": 5,  "is_active": True},
    {"name": "Entrées stock",  "path": "/entrees_stock",  "icon": "arrow-down-circle", "order": 6,  "is_active": True},
    {"name": "Sorties stock",  "path": "/sorties_stock",  "icon": "arrow-up-circle",   "order": 7,  "is_active": True},
    {"name": "Fournisseurs",   "path": "/fournisseurs",   "icon": "business-outline",  "order": 8,  "is_active": True},
    {"name": "Bons commande",  "path": "/bons_commande",  "icon": "document-text",     "order": 9,  "is_active": True},
    {"name": "Ordonnances",    "path": "/ordonnances",    "icon": "medkit",            "order": 10, "is_active": True},
    {"name": "Retours",        "path": "/retours",        "icon": "return-down-back",  "order": 11, "is_active": True},
    {"name": "Factures",       "path": "/factures",       "icon": "receipt",           "order": 12, "is_active": True},
    {"name": "Rapports",       "path": "/rapports",       "icon": "bar-chart",         "order": 13, "is_active": True},
    {"name": "Historique",     "path": "/historique",     "icon": "time",              "order": 14, "is_active": True},
    {"name": "Notifications",  "path": "/notifications",  "icon": "notifications",     "order": 15, "is_active": True},
    {"name": "Mon Compte",     "path": "/mon-compte",     "icon": "person-circle",     "order": 16, "is_active": True},
    {"name": "Employes",       "path": "/utilisateurs",   "icon": "people",            "order": 17, "is_active": True},
    {"name": "Pharmacies",     "path": "/pharmacies",     "icon": "business",          "order": 18, "is_active": True},
    {"name": "Rôles",          "path": "/roles",          "icon": "shield",            "order": 19, "is_active": True},
    {"name": "Menus",          "path": "/menus",          "icon": "menu",              "order": 20, "is_active": True},
    {"name": "Abonnements",    "path": "/abonnements",    "icon": "card",              "order": 21, "is_active": True},
    {"name": "Admin Panel",    "path": "/admin",          "icon": "shield-half",       "order": 22, "is_active": True},
    {"name": "Admin Alertes",  "path": "/admin",  "icon": "warning",           "order": 23, "is_active": True},
]

PERMISSIONS = {
    "admin": ["/admin", "/admin/alertes"],
    "proprietaire": [
        "/dashboard", "/produits", "/entrees_stock", "/sorties_stock",
        "/ventes", "/commandes", "/clients", "/fournisseurs", "/bons_commande",
        "/ordonnances", "/retours", "/factures", "/rapports", "/historique",
        "/notifications", "/mon-compte", "/utilisateurs", "/abonnements",
    ],
    "vendeur": [
        "/dashboard", "/produits", "/ventes", "/clients",
        "/ordonnances", "/retours", "/factures", "/notifications", "/mon-compte",
    ],
    "caissier": [
        "/dashboard", "/ventes", "/commandes", "/factures",
        "/clients", "/notifications", "/mon-compte",
    ],
    "gestionnaire_stock": [
        "/dashboard", "/produits", "/entrees_stock", "/sorties_stock",
        "/fournisseurs", "/bons_commande", "/rapports", "/historique",
        "/notifications", "/mon-compte",
    ],
}

# ─────────────────────────────────────────────────────────────
# 5. Initialisation idempotente
# ─────────────────────────────────────────────────────────────

def initialiser_base():
    db    = SessionLocal()
    stats = {"roles": 0, "menus_crees": 0, "menus_mis_a_jour": 0, "permissions": 0}

    try:
        # 5.1 Rôles
        roles_map: dict[str, Role] = {}
        for r in ROLES_DEFAUT:
            role = db.query(Role).filter(Role.name == r["name"]).first()
            if not role:
                role = Role(
                    name=r["name"], description=r["description"],
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                )
                db.add(role)
                db.flush()
                stats["roles"] += 1
            else:
                if role.description != r["description"]:
                    role.description = r["description"]
                    role.updated_at  = datetime.utcnow()
            roles_map[r["name"]] = role
        db.commit()

        # 5.2 Menus (upsert)
        menus_map: dict[str, Menu] = {}
        for m in MENUS_DEFAUT:
            menu = db.query(Menu).filter(Menu.path == m["path"]).first()
            if not menu:
                menu = Menu(
                    name=m["name"], path=m["path"], icon=m.get("icon", ""),
                    order=m.get("order", 99), is_active=m.get("is_active", True),
                    created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
                )
                db.add(menu)
                db.flush()
                stats["menus_crees"] += 1
            else:
                changed = False
                for field in ("name", "icon", "order"):
                    if getattr(menu, field) != m.get(field):
                        setattr(menu, field, m.get(field))
                        changed = True
                if changed:
                    menu.updated_at = datetime.utcnow()
                    stats["menus_mis_a_jour"] += 1
            menus_map[m["path"]] = menu
        db.commit()

        # 5.3 Permissions rôle ↔ menu
        for role_name, menu_paths in PERMISSIONS.items():
            role = roles_map.get(role_name)
            if not role:
                continue
            for path in menu_paths:
                menu = menus_map.get(path)
                if not menu:
                    continue
                existing = db.query(RoleMenu).filter_by(role_id=role.id, menu_id=menu.id).first()
                if not existing:
                    db.add(RoleMenu(role_id=role.id, menu_id=menu.id))
                    stats["permissions"] += 1
        db.commit()

        # 5.4 Compte admin système
        admin_email = os.getenv("ADMIN_EMAIL", "pharmy-cservice@gmail.com")
        admin_nom   = os.getenv("ADMIN_NOM",   "Pharmy-C Admin")

        _env            = os.getenv("ENV", "development")
        admin_password  = os.getenv("ADMIN_PASSWORD")

        if not admin_password:
            if _env == "production":
                raise ValueError(
                    "ADMIN_PASSWORD manquant dans .env — "
                    "définissez un mot de passe fort d'au moins 16 caractères."
                )
            else:
                admin_password = "PharMyCAdmin@Dev2024!"
                print("⚠️  ADMIN_PASSWORD non défini — mot de passe développement utilisé.")
                print("    Ajoutez ADMIN_PASSWORD=... dans votre .env pour supprimer cet avertissement.")

        admin_user = db.query(Utilisateur).filter(Utilisateur.email == admin_email).first()
        if not admin_user:
            admin_role = roles_map.get("admin")
            if admin_role:
                admin_user = Utilisateur(
                    uuid               = str(uuid.uuid4()),
                    nom                = admin_nom,
                    email              = admin_email,
                    mot_de_passe       = get_password_hash(admin_password),
                    id_role            = admin_role.id,
                    est_actif          = True,
                    confirmation_email = True,
                    actif              = True,
                )
                db.add(admin_user)
                db.commit()
                print(f"  ✅ Compte admin créé : {admin_email}")
        else:
            print(f"  ℹ️  Compte admin existant : {admin_email}")

        print(f"""
╔══════════════════════════════════════════════════════╗
║         🚀 Pharmy-C API v4.2 — Prête !               ║
╠══════════════════════════════════════════════════════╣
║  Rôles créés        : {stats['roles']:<4}                           ║
║  Menus créés        : {stats['menus_crees']:<4}                           ║
║  Menus mis à jour   : {stats['menus_mis_a_jour']:<4}                           ║
║  Permissions créées : {stats['permissions']:<4}                           ║
╚══════════════════════════════════════════════════════╝
        """)

    except Exception as e:
        print(f"\n❌ Erreur initialisation : {e}")
        db.rollback()
        raise
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# 6. Lifespan
# ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    initialiser_base()
    yield
    print("👋 Pharmy-C API arrêtée proprement")


# ─────────────────────────────────────────────────────────────
# 7. Application FastAPI
# ─────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "Pharmy-C API",
    version     = "4.2",
    description = (
        "API de gestion complète de pharmacies — multi-tenant SaaS.\n\n"
        "**Modules :** stock, ventes, clients, fournisseurs, ordonnances, "
        "retours, facturation, rapports & statistiques."
    ),
    contact  = {"name": "Phary-C Support", "email": "pharmy-cservice@gmail.com"},
    lifespan = lifespan,
)

if _rate_limit_available:
    app.state.limiter = limiter


# ═════════════════════════════════════════════════════════════
# GESTION D'ERREURS CENTRALISÉE — Format unifié
#
# Toutes les erreurs backend retournent :
# {
#   "status":  "error" | "warning" | "success",
#   "code":    <HTTP int>,
#   "message": <message lisible par l'humain>,
#   "details": [<liste de détails optionnels>]   # null si absent
# }
#
# Règle status :
#   - 401, 403, 404, 409, 429 → "warning"  (problème utilisateur/accès)
#   - 400, 422               → "warning"  (données invalides)
#   - 5xx                    → "error"    (erreur serveur)
# ═════════════════════════════════════════════════════════════

def _build_response(
    status_str: str,
    code: int,
    message: str,
    details: list = None,
) -> JSONResponse:
    """Construit la réponse JSON standardisée."""
    return JSONResponse(
        status_code=code,
        content={
            "status":  status_str,
            "code":    code,
            "message": message,
            "details": details,
        },
    )


# ── 1. HTTPException (erreurs métier explicites du code) ─────
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Toutes les HTTPException levées dans les routers.
    Ex: raise HTTPException(404, "Produit introuvable")
    """
    code = exc.status_code

    # Warnings : problèmes côté client ou accès
    if code in (400, 401, 403, 404, 409, 422, 429):
        status_str = "warning"
    else:
        status_str = "error"

    # Extraire le message — detail peut être str ou dict
    if isinstance(exc.detail, dict):
        message = exc.detail.get("message") or exc.detail.get("detail") or str(exc.detail)
        details = exc.detail.get("details")
    elif isinstance(exc.detail, list):
        message = "Requête invalide"
        details = [str(d) for d in exc.detail]
    else:
        message = str(exc.detail) if exc.detail else _default_message(code)
        details = None

    logger.warning(f"[HTTPException {code}] {request.method} {request.url.path} — {message}")

    return _build_response(status_str, code, message, details)


# ── 2. RequestValidationError (422 — payload invalide) ───────
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Erreurs de validation Pydantic sur le body/query/path.
    Ex: champ manquant, type incorrect, valeur hors range.
    """
    errors = exc.errors()
    details = []
    for err in errors:
        field = " → ".join(str(loc) for loc in err.get("loc", []) if loc != "body")
        msg   = err.get("msg", "Valeur invalide")
        if field:
            details.append(f"{field} : {msg}")
        else:
            details.append(msg)

    # Message principal : premier champ en erreur
    if details:
        message = f"Données invalides : {details[0]}"
        if len(details) > 1:
            message += f" (et {len(details) - 1} autre(s) erreur(s))"
    else:
        message = "Les données envoyées sont invalides"

    logger.warning(f"[ValidationError 422] {request.method} {request.url.path} — {details}")

    return _build_response("warning", 422, message, details if len(details) > 1 else None)


# ── 3. Pydantic ValidationError interne (rare, hors request) ─
@app.exception_handler(ValidationError)
async def pydantic_validation_handler(request: Request, exc: ValidationError):
    """
    ValidationError Pydantic levée en dehors du cycle de requête
    (ex: dans un service ou un schema instancié manuellement).
    """
    errors  = exc.errors()
    details = [f"{' → '.join(str(l) for l in e.get('loc', []))}: {e.get('msg','')}" for e in errors]
    message = "Erreur de validation interne"

    logger.error(f"[PydanticValidation 422] {request.url.path} — {details}")

    return _build_response("warning", 422, message, details)


# ── 4. SQLAlchemy IntegrityError (contrainte DB) ─────────────
@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    """
    Violation de contrainte (UNIQUE, FK, NOT NULL, etc.).
    Ex: email déjà utilisé, code en doublon, FK invalide.
    """
    err_str = str(exc.orig).lower() if exc.orig else str(exc).lower()

    if "unique" in err_str or "duplicate" in err_str:
        message = "Cette entrée existe déjà (valeur en doublon)"
        code    = 409
        status_str = "warning"
    elif "foreign key" in err_str or "violates foreign key" in err_str:
        message = "Référence invalide : l'entité liée n'existe pas ou ne peut pas être supprimée"
        code    = 400
        status_str = "warning"
    elif "not null" in err_str or "null value" in err_str:
        message = "Un champ obligatoire est manquant"
        code    = 400
        status_str = "warning"
    else:
        message = "Erreur de cohérence des données"
        code    = 409
        status_str = "warning"

    logger.error(f"[IntegrityError {code}] {request.method} {request.url.path} — {exc.orig}")

    return _build_response(status_str, code, message, None)


# ── 5. SQLAlchemy OperationalError (connexion DB) ────────────
@app.exception_handler(OperationalError)
async def operational_error_handler(request: Request, exc: OperationalError):
    """
    Erreur de connexion ou d'opération PostgreSQL.
    Ex: serveur DB injoignable, timeout, colonne inexistante.
    """
    logger.critical(f"[OperationalError 503] {request.method} {request.url.path} — {exc}")

    return _build_response(
        "error", 503,
        "Service temporairement indisponible — problème de base de données. Réessayez dans quelques instants.",
        None,
    )


# ── 6. SQLAlchemy DataError (données mal typées côté DB) ─────
@app.exception_handler(DataError)
async def data_error_handler(request: Request, exc: DataError):
    """
    Valeur hors range ou type incompatible avec le schéma DB.
    Ex: texte trop long, nombre trop grand pour Numeric(10,2).
    """
    logger.error(f"[DataError 400] {request.method} {request.url.path} — {exc}")

    return _build_response(
        "warning", 400,
        "Les données envoyées dépassent les limites autorisées (valeur trop grande ou type incorrect)",
        None,
    )


# ── 7. SQLAlchemyError générique ─────────────────────────────
@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    """
    Toute autre erreur SQLAlchemy non capturée ci-dessus.
    """
    logger.error(f"[SQLAlchemyError 500] {request.method} {request.url.path} — {exc}")

    return _build_response(
        "error", 500,
        "Erreur interne de base de données",
        None,
    )


# ── 8. RateLimitExceeded (slowapi) ───────────────────────────
if _rate_limit_available:
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
        """Trop de requêtes en peu de temps."""
        logger.warning(f"[RateLimit 429] {request.client.host} — {request.url.path}")
        return _build_response(
            "warning", 429,
            "Trop de requêtes — veuillez patienter quelques secondes avant de réessayer",
            None,
        )


# ── 9. Exception générique (fallback ultime) ─────────────────
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """
    Filet de sécurité : capture toute exception non gérée.
    Ne renvoie JAMAIS le traceback au client (sécurité).
    Log complet côté serveur.
    """
    logger.error(
        f"[UnhandledException 500] {request.method} {request.url.path}\n"
        + traceback.format_exc()
    )

    _env = os.getenv("ENV", "development")
    if _env == "development":
        # En dev, on peut montrer un peu plus d'info
        details = [f"{type(exc).__name__}: {str(exc)[:200]}"]
    else:
        details = None

    return _build_response(
        "error", 500,
        "Une erreur inattendue s'est produite. Notre équipe a été notifiée.",
        details,
    )


# ─── Helper : message par défaut selon code HTTP ─────────────
def _default_message(code: int) -> str:
    return {
        400: "Requête invalide",
        401: "Authentification requise",
        403: "Accès refusé — vous n'avez pas les droits nécessaires",
        404: "Ressource introuvable",
        405: "Méthode non autorisée",
        409: "Conflit — cette ressource existe déjà",
        410: "Ressource supprimée définitivement",
        422: "Données invalides",
        429: "Trop de requêtes",
        500: "Erreur interne du serveur",
        502: "Erreur passerelle",
        503: "Service indisponible",
    }.get(code, f"Erreur HTTP {code}")


# ─────────────────────────────────────────────────────────────
# 8. CORS — strict, pas de "*" par défaut en production
# ─────────────────────────────────────────────────────────────

_env         = os.getenv("ENV", "development")
_origins_str = os.getenv("ALLOWED_ORIGINS", "")

if not _origins_str:
    if _env == "production":
        raise ValueError(
            "ALLOWED_ORIGINS doit être défini en production.\n"
            "Exemple : ALLOWED_ORIGINS=https://votre-app.com,exp://192.168.1.x:8081"
        )
    ALLOWED_ORIGINS = ["*"]
    print("⚠️  CORS ouvert (*) — mode développement. Définissez ALLOWED_ORIGINS en production.")
else:
    ALLOWED_ORIGINS = [o.strip() for o in _origins_str.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ALLOWED_ORIGINS,
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ─────────────────────────────────────────────────────────────
# 9. Fichiers statiques
# ─────────────────────────────────────────────────────────────

for folder in ["uploads/logos", "uploads/ordonnances", "uploads/factures", "uploads/paiements"]:
    os.makedirs(folder, exist_ok=True)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# ─────────────────────────────────────────────────────────────
# 10. Routeurs
# ─────────────────────────────────────────────────────────────

app.include_router(auth.router,          prefix="/auth",          tags=["🔐 Auth"])
app.include_router(dashboard.router,     prefix="/dashboard",     tags=["📊 Dashboard"])
app.include_router(utilisateurs.router,  prefix="/utilisateurs",  tags=["👤 Employes"])
app.include_router(pharmacies.router,    prefix="/pharmacies",    tags=["🏥 Pharmacies"])
app.include_router(produits.router,      prefix="/produits",      tags=["💊 Produits"])
app.include_router(entrees_stock.router, prefix="/entrees_stock", tags=["📦 Entrées Stock"])
app.include_router(sorties_stock.router, prefix="/sorties_stock", tags=["📤 Sorties Stock"])
app.include_router(ventes.router,        prefix="/ventes",        tags=["🛒 Ventes"])
app.include_router(historique.router,    prefix="/historique",    tags=["📜 Historique"])
app.include_router(roles.router,         prefix="/roles",         tags=["🛡️ Rôles"])
app.include_router(menus.router,         prefix="/menus",         tags=["📋 Menus"])
app.include_router(clients.router,       prefix="/clients",       tags=["🧑 Clients"])
app.include_router(notifications.router, prefix="/notifications", tags=["🔔 Notifications"])
app.include_router(fournisseurs.router,  prefix="/fournisseurs",  tags=["🚚 Fournisseurs"])
app.include_router(bons_commande.router, prefix="/bons_commande", tags=["📄 Bons de commande"])
app.include_router(ordonnances.router,   prefix="/ordonnances",   tags=["📋 Ordonnances"])
app.include_router(retours.router,       prefix="/retours",       tags=["↩ Retours"])
app.include_router(factures.router,      prefix="/factures",      tags=["🧾 Facturation"])
app.include_router(rapports.router,      prefix="/rapports",      tags=["📊 Rapports"])
app.include_router(prix_fournisseur.router, prefix="/prix-fournisseur", tags=["💰 Prix fournisseur"])
app.include_router(abonnements.router,    prefix="/abonnements",   tags=["💳 Abonnements"])
app.include_router(admin_router.router,   prefix="/admin",         tags=["👑 Admin"])

# ── Module financier (v4.2) ─────────────────────────────────
try:
    from routers import rapports_finance as finance_router
    app.include_router(finance_router.router, prefix="/finance", tags=["💰 Finance"])
except ImportError as e:
    print(f"⚠️  Module finance non disponible : {e}")

# ── Messagerie (v4.2) ─────────────────────────────────────
if _messages_ok:
    app.include_router(messages_router.router, prefix="/messages", tags=["💬 Messages"])

# ─────────────────────────────────────────────────────────────
# 11. Routes utilitaires
# ─────────────────────────────────────────────────────────────

@app.get("/", tags=["🏠 Accueil"])
def read_root():
    return {
        "status":  "success",
        "message": "Bienvenue sur Pharmy-C API",
        "version": "4.2",
        "docs":    "/docs",
        "redoc":   "/redoc",
    }


@app.get("/health", tags=["🏠 Accueil"])
def health_check():
    db_status     = "error"
    db_latency_ms = None
    try:
        db    = SessionLocal()
        start = datetime.utcnow()
        db.execute(text("SELECT 1"))
        db_latency_ms = round((datetime.utcnow() - start).total_seconds() * 1000, 2)
        db.close()
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)[:50]}"

    overall = "ok" if db_status == "ok" else "degraded"
    return {
        "status":    overall,
        "version":   "4.2",
        "database":  {"status": db_status, "latency_ms": db_latency_ms},
        "timestamp": datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────
# 12. Point d'entrée développement
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host      = "0.0.0.0",
        port      = int(os.getenv("PORT", 8000)),
        reload    = os.getenv("ENV", "development") == "development",
        workers   = 1,
        log_level = "info",
    )