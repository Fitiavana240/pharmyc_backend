# create_tables.py
# Lance ce fichier UNE FOIS pour créer toutes les tables en base.
# Commande : python create_tables.py

from database import Base, engine

# --- Modèles existants ---
from models.models import (
    Pharmacie, Role, Menu, RoleMenu, Utilisateur, Client,
    Produit, EntreeStock, SortieStock, Vente, DetailVente,
    Paiement, Notification, Historique
)

# --- Nouveaux modèles ---
from models.fournisseurs import Fournisseur, BonCommande, LigneBonCommande
from models.ordonnances  import Ordonnance, LigneOrdonnance
from models.retours      import RetourProduit, LigneRetour
from models.factures     import Facture

print("📦 Création des tables dans PostgreSQL...")
Base.metadata.create_all(bind=engine)
print("✅ Toutes les tables créées avec succès !")
print("\nTables créées :")
for table in sorted(Base.metadata.tables.keys()):
    print(f"  • {table}")
