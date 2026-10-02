import subprocess
from google.oauth2.credentials import Credentials
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-01-bc4ed232583b"

def get_gcloud_credentials():
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
    return Credentials(token)

def seed_recipes():
    print(f"Connecting to Firestore for project: {PROJECT_ID}")
    creds = get_gcloud_credentials()
    db = firestore.Client(project=PROJECT_ID, credentials=creds)
    recipes_ref = db.collection("recipes")

    sample_recipes = [
        {
            "id": "avocado-egg-toast",
            "title": "Avocado & Poached Egg Toast",
            "category": "Breakfast",
            "prep_time_minutes": 15,
            "difficulty": "Easy",
            "ingredients": [
                "2 slices sourdough bread",
                "1 ripe avocado",
                "2 large eggs",
                "1 tsp red pepper flakes",
                "Salt and pepper to taste"
            ],
            "instructions": [
                "Toast sourdough bread until golden brown.",
                "Mash avocado with salt and pepper in a small bowl.",
                "Poach eggs in simmering water for 3 minutes.",
                "Spread avocado on toast, top with poached eggs and red pepper flakes."
            ],
            "dietary_tags": ["Vegetarian", "Dairy-Free"],
            "calories": 380,
            "protein_g": 16,
        },
        {
            "id": "quinoa-buddha-bowl",
            "title": "Mediterranean Quinoa Buddha Bowl",
            "category": "Lunch",
            "prep_time_minutes": 25,
            "difficulty": "Easy",
            "ingredients": [
                "1 cup cooked quinoa",
                "1/2 cup chickpeas",
                "1/2 cup cherry tomatoes halved",
                "1/2 cucumber diced",
                "1/4 cup feta cheese",
                "2 tbsp tahini dressing"
            ],
            "instructions": [
                "Cook quinoa according to package instructions and cool slightly.",
                "Arrange quinoa, chickpeas, tomatoes, and cucumber in a bowl.",
                "Crumble feta cheese over the top and drizzle with tahini dressing."
            ],
            "dietary_tags": ["Vegetarian", "Gluten-Free"],
            "calories": 450,
            "protein_g": 18,
        },
        {
            "id": "grilled-lemon-herb-salmon",
            "title": "Grilled Lemon Herb Salmon",
            "category": "Dinner",
            "prep_time_minutes": 20,
            "difficulty": "Medium",
            "ingredients": [
                "2 salmon fillets (6 oz each)",
                "2 tbsp olive oil",
                "1 lemon juiced and zested",
                "2 cloves minced garlic",
                "1 tbsp fresh dill chopped"
            ],
            "instructions": [
                "Whisk olive oil, lemon juice, lemon zest, garlic, and dill.",
                "Marinate salmon fillets for 10 minutes.",
                "Grill over medium-high heat for 4-5 minutes per side until flaky."
            ],
            "dietary_tags": ["Gluten-Free", "Keto", "High-Protein", "Dairy-Free"],
            "calories": 520,
            "protein_g": 42,
        },
    ]

    for recipe in sample_recipes:
        doc_id = recipe["id"]
        recipes_ref.document(doc_id).set(recipe)
        print(f"✅ Seeded recipe: '{recipe['title']}' (ID: {doc_id})")

    print("\n🎉 Firestore seeding complete!")

if __name__ == "__main__":
    seed_recipes()
