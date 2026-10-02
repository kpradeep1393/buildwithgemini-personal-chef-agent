# ruff: noqa
import base64
import io
import json
import os
import subprocess
import urllib.parse
import urllib.request
from typing import Optional
from dotenv import load_dotenv
from PIL import Image, ImageDraw

from google import genai
from google.cloud import firestore, storage
from google.oauth2.credentials import Credentials

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager

from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.genai import types

from app.a2ui_utils import a2ui_callback

load_dotenv()

# HARDCODED PROJECT ID string to prevent Agent Platform project number resolution issues
PROJECT_ID = "qwiklabs-gcp-01-bc4ed232583b"
BUCKET_NAME = "personal-chef-images-qwiklabs-gcp-01-bc4ed232583b"
LOCATION = "us-central1"

# Set Enterprise / Vertex AI mode environment variables for google.genai Client
os.environ["GOOGLE_GENAI_USE_ENTERPRISE"] = "true"
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"] = LOCATION

# Memory Bank ID (reusing Agent Engine ID from deployment_metadata.json)
MEMORY_BANK_ID = "3167103111634354176"
os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ID", MEMORY_BANK_ID)

# Load Agent Engine resource name from deployment_metadata.json if available
METADATA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "deployment_metadata.json")
AGENT_ENGINE_RESOURCE_NAME = f"projects/{PROJECT_ID}/locations/{LOCATION}/reasoningEngines/{MEMORY_BANK_ID}"
if os.path.exists(METADATA_PATH):
    try:
        with open(METADATA_PATH, "r") as f:
            meta = json.load(f)
            AGENT_ENGINE_RESOURCE_NAME = meta.get("remote_agent_runtime_id") or AGENT_ENGINE_RESOURCE_NAME
    except Exception:
        pass


def get_db_client() -> firestore.Client:
    """Helper to initialize Firestore client using hardcoded GCP project ID."""
    try:
        token = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
        creds = Credentials(token)
        return firestore.Client(project=PROJECT_ID, credentials=creds)
    except Exception:
        return firestore.Client(project=PROJECT_ID)


def get_maps_api_key() -> str:
    """Retrieve Google Maps API key from environment variable."""
    return os.getenv("GOOGLE_MAPS_API_KEY", "")


async def generate_memories_callback(callback_context: CallbackContext):
    """Callback triggered after each turn to extract and persist durable facts and user recipes to Memory Bank."""
    try:
        await callback_context.add_session_to_memory()
    except Exception:
        pass
    return None


def generate_ai_recipe_image(prompt: str, tool_context: ToolContext) -> dict:
    """Generate a realistic AI image for a recipe, dish, or ingredient using gemini-3.1-flash-lite-image,
    save it as an artifact, and publish it to public Cloud Storage.

    Args:
        prompt: Description of the food dish or recipe image to generate (e.g., 'A gourmet chicken pasta dish').
        tool_context: ADK ToolContext provided automatically by the agent framework.

    Returns:
        Dictionary containing status, public GCS image URL, and artifact filename.
    """
    try:
        genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")
        response = genai_client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        image_bytes = None
        mime_type = "image/jpeg"

        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    image_bytes = part.inline_data.data
                    mime_type = part.inline_data.mime_type or "image/jpeg"
                    break

        if not image_bytes:
            return {"error": "Failed to generate image from gemini-3.1-flash-lite-image model."}

        clean_slug = "".join(c if c.isalnum() else "_" for c in prompt[:30].lower()).strip("_")
        ext = "jpg" if "jpeg" in mime_type or "jpg" in mime_type else "png"
        filename = f"recipe_{clean_slug}.{ext}"

        # 1. Save artifact for Playground Artifacts panel
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload image bytes directly to public GCS bucket
        try:
            token = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
            storage_client = storage.Client(project=PROJECT_ID, credentials=Credentials(token))
        except Exception:
            storage_client = storage.Client(project=PROJECT_ID)

        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

        return {
            "status": "success",
            "prompt": prompt,
            "filename": filename,
            "public_url": public_url,
            "message": f"Generated AI image, saved artifact '{filename}', and published to Cloud Storage.",
        }
    except Exception as e:
        return {"error": f"AI Image generation failed: {str(e)}"}


def generate_ai_recipe_video(prompt: str, tool_context: ToolContext) -> dict:
    """Generate a short video for a recipe, dish, cooking technique, or culinary item using Google's Omni model (gemini-omni-flash-preview)
    in the global region, save it as an artifact, and upload it directly to public Cloud Storage.

    Args:
        prompt: Description of the food video or cooking technique to generate (e.g., 'A chef tossing a fresh garden salad', 'Slicing a steak').
        tool_context: ADK ToolContext provided automatically by the agent framework.

    Returns:
        Dictionary containing status, public GCS video URL, and artifact filename.
    """
    try:
        genai_client = genai.Client(vertexai=True, project=PROJECT_ID, location="global")

        interaction = genai_client.interactions.create(
            model="gemini-omni-flash-preview",
            input=[{"type": "text", "text": prompt}],
            response_format=[{"type": "video", "duration": "5s", "aspect_ratio": "16:9"}],
            generation_config={"video_config": {"task": "text_to_video"}},
        )

        video_bytes = None
        mime_type = "video/mp4"

        if hasattr(interaction, "steps") and interaction.steps:
            for step in interaction.steps:
                if hasattr(step, "content") and step.content:
                    for item in step.content:
                        raw_data = getattr(item, "bytes", None)
                        if raw_data:
                            if isinstance(raw_data, str):
                                video_bytes = base64.b64decode(raw_data)
                            elif isinstance(raw_data, bytes):
                                video_bytes = raw_data
                            mime_type = getattr(item, "mime_type", None) or "video/mp4"
                            break
                    if video_bytes:
                        break

        if not video_bytes:
            return {"error": "Failed to generate or extract video bytes from gemini-omni-flash-preview model."}

        clean_slug = "".join(c if c.isalnum() else "_" for c in prompt[:30].lower()).strip("_")
        filename = f"recipe_video_{clean_slug}.mp4"

        # 1. Save artifact with tool_context.save_artifact so it shows up in Playground's Artifacts panel
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type=mime_type)
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # 2. Upload video bytes directly to public Cloud Storage bucket
        try:
            token = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
            storage_client = storage.Client(project=PROJECT_ID, credentials=Credentials(token))
        except Exception:
            storage_client = storage.Client(project=PROJECT_ID)

        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

        return {
            "status": "success",
            "prompt": prompt,
            "filename": filename,
            "public_url": public_url,
            "message": f"Generated AI video, saved artifact '{filename}', and published to Cloud Storage.",
        }
    except Exception as e:
        return {"error": f"AI Video generation failed: {str(e)}"}


def geocode_address(address: str) -> dict:
    """Convert a physical address or city name into geographical coordinates (latitude and longitude).

    Args:
        address: Full or partial address, city, or landmark string (e.g., '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        Dictionary containing formatted address, latitude, and longitude.
    """
    api_key = get_maps_api_key()
    if not api_key:
        return {"error": "GOOGLE_MAPS_API_KEY environment variable is missing."}

    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={urllib.parse.quote(address)}&key={api_key}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("status") != "OK" or not data.get("results"):
                return {"error": f"Geocoding failed for address '{address}': {data.get('status')}"}

            result = data["results"][0]
            loc = result["geometry"]["location"]
            return {
                "name": address,
                "formatted_address": result.get("formatted_address"),
                "location": {
                    "latitude": loc.get("lat"),
                    "longitude": loc.get("lng"),
                },
            }
    except Exception as e:
        return {"error": f"Geocoding API error: {str(e)}"}


def find_nearby_places(
    latitude: float,
    longitude: float,
    place_type: str = "grocery_store",
    radius_meters: float = 2000.0,
) -> list[dict]:
    """Search for nearby places of a given type around geographical coordinates using Places API (New).

    Args:
        latitude: Latitude coordinate.
        longitude: Longitude coordinate.
        place_type: Type of place to find (e.g., 'grocery_store', 'supermarket', 'bakery', 'restaurant', 'store').
        radius_meters: Search radius in meters (default 2000m).

    Returns:
        List of nearby places with name, formatted address, and location coordinates.
    """
    api_key = get_maps_api_key()
    if not api_key:
        return [{"error": "GOOGLE_MAPS_API_KEY environment variable is missing."}]

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }

    payload = {
        "includedTypes": [place_type],
        "maxResultCount": 5,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": latitude,
                    "longitude": longitude,
                },
                "radius": radius_meters,
            }
        },
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            places = data.get("places", [])

            results = []
            for place in places:
                results.append({
                    "name": place.get("displayName", {}).get("text"),
                    "formatted_address": place.get("formattedAddress"),
                    "location": place.get("location"),
                })
            return results
    except Exception as e:
        return [{"error": f"Places API (New) error: {str(e)}"}]


def search_recipes(
    category: Optional[str] = None,
    max_prep_time: Optional[int] = None,
    dietary_tag: Optional[str] = None,
) -> list[dict]:
    """Search for recipes in the local Firestore database by category, prep time limit, or dietary requirements.

    Args:
        category: Optional category filter such as 'Breakfast', 'Lunch', or 'Dinner'.
        max_prep_time: Optional maximum prep time limit in minutes.
        dietary_tag: Optional dietary label such as 'Vegetarian', 'Gluten-Free', 'Keto', or 'Dairy-Free'.

    Returns:
        List of matching recipes with full details.
    """
    db = get_db_client()
    recipes_ref = db.collection("recipes")
    docs = recipes_ref.stream()

    results = []
    for doc in docs:
        recipe = doc.to_dict()
        recipe["id"] = doc.id

        if category and recipe.get("category", "").lower() != category.lower():
            continue
        if max_prep_time and recipe.get("prep_time_minutes", 999) > max_prep_time:
            continue
        if dietary_tag:
            tags = [t.lower() for t in recipe.get("dietary_tags", [])]
            if dietary_tag.lower() not in tags:
                continue

        results.append(recipe)

    return results


def get_recipe_details(recipe_id: str) -> dict:
    """Get complete details for a specific recipe by its unique ID.

    Args:
        recipe_id: Unique string identifier for the recipe (e.g., 'avocado-egg-toast', 'quinoa-buddha-bowl').

    Returns:
        Dictionary containing full recipe details including ingredients, instructions, and nutritional info.
    """
    db = get_db_client()
    doc_ref = db.collection("recipes").document(recipe_id)
    doc = doc_ref.get()

    if not doc.exists:
        return {"error": f"Recipe with ID '{recipe_id}' not found in the database."}

    data = doc.to_dict()
    data["id"] = doc.id
    return data


def add_recipe(
    title: str,
    category: str,
    prep_time_minutes: int,
    difficulty: str,
    ingredients: list[str],
    instructions: list[str],
    dietary_tags: list[str],
    calories: int = 0,
    protein_g: int = 0,
) -> dict:
    """Save a new recipe to the Firestore database and remember it in memory.

    Args:
        title: Name of the dish (e.g., 'Berry Protein Smoothie').
        category: Category such as 'Breakfast', 'Lunch', 'Dinner', 'Snack', or 'Dessert'.
        prep_time_minutes: Preparation time in minutes.
        difficulty: Skill level ('Easy', 'Medium', 'Hard').
        ingredients: List of ingredients with measurements.
        instructions: Step-by-step cooking steps.
        dietary_tags: List of dietary tags such as ['Vegan', 'Gluten-Free'].
        calories: Estimated calories per serving.
        protein_g: Estimated protein in grams per serving.

    Returns:
        Dictionary confirming creation with recipe ID, title, and detailed summary for memory extraction.
    """
    db = get_db_client()
    recipe_id = title.lower().replace(" ", "-").replace("&", "and")
    recipe_id = "".join(c for c in recipe_id if c.isalnum() or c == "-")

    recipe_data = {
        "id": recipe_id,
        "title": title,
        "category": category,
        "prep_time_minutes": prep_time_minutes,
        "difficulty": difficulty,
        "ingredients": ingredients,
        "instructions": instructions,
        "dietary_tags": dietary_tags,
        "calories": calories,
        "protein_g": protein_g,
    }

    db.collection("recipes").document(recipe_id).set(recipe_data)

    ing_summary = ", ".join(ingredients)
    inst_summary = "; ".join(instructions)

    return {
        "status": "success",
        "message": f"Recipe '{title}' saved to database and remembered in memory! Ingredients: {ing_summary}. Instructions: {inst_summary}",
        "recipe_id": recipe_id,
        "recipe_details": recipe_data,
    }


def generate_recipe_image(recipe_id: str) -> dict:
    """Generate a visual presentation card/image for a recipe and save it to Cloud Storage.

    Args:
        recipe_id: Unique string identifier for the recipe (e.g., 'avocado-egg-toast', 'quinoa-buddha-bowl').

    Returns:
        Dictionary containing the public image URL and updated recipe information.
    """
    db = get_db_client()
    doc_ref = db.collection("recipes").document(recipe_id)
    doc = doc_ref.get()

    if not doc.exists:
        return {"error": f"Recipe with ID '{recipe_id}' not found in database."}

    recipe = doc.to_dict()
    title = recipe.get("title", "Delicious Recipe")
    category = recipe.get("category", "General")
    prep_time = recipe.get("prep_time_minutes", 15)
    difficulty = recipe.get("difficulty", "Easy")
    ingredients = ", ".join(recipe.get("ingredients", [])[:4])

    img = Image.new("RGB", (600, 360), color=(248, 250, 252))
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, 600, 100], fill=(234, 88, 12))
    draw.text((30, 35), "PERSONAL CHEF RECIPE CARD", fill=(255, 255, 255))

    draw.text((30, 130), f"Dish: {title}", fill=(15, 23, 42))
    draw.text((30, 180), f"Category: {category}  |  Prep: {prep_time} mins  |  {difficulty}", fill=(100, 116, 139))
    draw.text((30, 240), f"Key Ingredients: {ingredients}", fill=(51, 65, 85))
    draw.text((30, 290), "Status: Verified & Ready to Cook!", fill=(16, 185, 129))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    image_bytes = buf.getvalue()

    try:
        token = subprocess.check_output(["gcloud", "auth", "print-access-token"]).decode().strip()
        storage_client = storage.Client(project=PROJECT_ID, credentials=Credentials(token))
    except Exception:
        storage_client = storage.Client(project=PROJECT_ID)

    bucket = storage_client.bucket(BUCKET_NAME)
    filename = f"{recipe_id}.jpg"
    blob = bucket.blob(filename)
    blob.upload_from_string(image_bytes, content_type="image/jpeg")

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

    doc_ref.update({"image_url": public_url})

    return {
        "status": "success",
        "recipe_id": recipe_id,
        "title": title,
        "image_url": public_url,
        "message": f"Generated and published recipe card image for '{title}'.",
    }


def search_global_recipes(query: str) -> list[dict]:
    """Fetch real-world recipe suggestions and cooking instructions from TheMealDB public API.

    Args:
        query: Food or dish name search term (e.g., 'pasta', 'chicken', 'taco', 'curry', 'salmon').

    Returns:
        List of matching real recipes with title, category, cuisine, ingredients, and instructions.
    """
    terms = [w.strip().lower() for w in query.strip().split() if len(w.strip()) > 2]

    candidate_queries = [query.strip()]
    if "pasta" in query.lower():
        candidate_queries.extend(["alfredo", "spaghetti", "fettuccine", "pasta"])
    if terms:
        candidate_queries.extend(terms)

    collected_meals = []
    seen_ids = set()

    for q in candidate_queries:
        url = f"https://www.themealdb.com/api/json/v1/1/search.php?s={urllib.parse.quote(q)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PersonalChefAgent/1.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
                meals = data.get("meals") or []
                for meal in meals:
                    meal_id = meal.get("idMeal")
                    if meal_id and meal_id not in seen_ids:
                        seen_ids.add(meal_id)
                        collected_meals.append(meal)
        except Exception:
            continue

    if not collected_meals:
        return []

    if len(terms) > 1:
        def match_score(m):
            text = f"{m.get('strMeal', '')} {m.get('strCategory', '')} {' '.join(m.get(f'strIngredient{i}', '') or '' for i in range(1, 21))}".lower()
            return sum(1 for t in terms if t in text)

        collected_meals.sort(key=match_score, reverse=True)

    results = []
    for meal in collected_meals[:3]:
        ingredients = []
        for i in range(1, 21):
            ing = meal.get(f"strIngredient{i}")
            meas = meal.get(f"strMeasure{i}")
            if ing and ing.strip():
                ingredients.append(f"{meas.strip() if meas else ''} {ing.strip()}".strip())

        results.append({
            "title": meal.get("strMeal"),
            "category": meal.get("strCategory"),
            "cuisine": meal.get("strArea"),
            "instructions": meal.get("strInstructions"),
            "thumbnail_url": meal.get("strMealThumb"),
            "ingredients": ingredients,
        })

    return results


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are a warm, knowledgeable Personal Chef and Recipe Assistant. "
        "You remember all user preferences, dietary restrictions, custom recipes, and culinary facts from previous conversations and use them to personalize your responses. "
        "IMPORTANT: Always make sure all recipes created, added, modified, or requested by the user are recorded and remembered in memory across sessions. "
        "Use your database tools to search for local recipes in Firestore, retrieve recipe details, "
        "save new custom recipes, generate image cards using generate_recipe_image, "
        "fetch external real-world recipes using search_global_recipes, "
        "generate AI food images using generate_ai_recipe_image, "
        "generate short food/recipe videos using generate_ai_recipe_video, "
        "and use geocode_address and find_nearby_places to find grocery stores or markets near the user. "
        "You can also write and execute Python code in your Agent Platform sandbox to solve culinary math, portion scaling, or nutritional calculations."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        client_kwargs={
            "vertexai": True,
            "project": PROJECT_ID,
            "location": LOCATION,
        },
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    tools=[
        PreloadMemoryTool(),
        search_recipes,
        get_recipe_details,
        add_recipe,
        generate_recipe_image,
        search_global_recipes,
        generate_ai_recipe_image,
        generate_ai_recipe_video,
        geocode_address,
        find_nearby_places,
    ],
    after_model_callback=a2ui_callback,
    after_agent_callback=generate_memories_callback,
    code_executor=AgentEngineSandboxCodeExecutor(
        agent_engine_resource_name=AGENT_ENGINE_RESOURCE_NAME,
    ),
)

app = App(
    root_agent=root_agent,
    name="app",
)
