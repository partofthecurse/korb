"""Food catalog: maps item names to an icon, a store department and a tile color."""

import re
import unicodedata
from pathlib import Path

CATEGORIES = [
    "Obst und Gemüse", "Snacks und Süßes", "Limo und Bier", "Brot und Eier",
    "Milchprodukte", "Pasta und Tomatensachen", "TK", "Fleischtheke",
    "Konserven", "Haushaltsartikel", "Wein und Spirituosen", "Sonstiges",
]
FALLBACK_CATEGORY = "Sonstiges"

COLORS = {
    "mint": "Mint", "yellow": "Gelb", "coral": "Koralle",
    "rose": "Rosa", "blue": "Blau", "lilac": "Flieder",
}
FALLBACK_ICON = "1f6d2"
FALLBACK_COLOR = "blue"

ICON_LABELS = {
    "1f347": "Trauben", "1f348": "Melone", "1f349": "Wassermelone", "1f34a": "Mandarine",
    "1f34b": "Zitrone", "1f34c": "Banane", "1f34d": "Ananas", "1f96d": "Mango",
    "1f34e": "Roter Apfel", "1f34f": "Grüner Apfel", "1f350": "Birne", "1f351": "Pfirsich",
    "1f352": "Kirschen", "1f353": "Erdbeere", "1fad0": "Heidelbeeren", "1f95d": "Kiwi",
    "1f345": "Tomate", "1fad2": "Olive", "1f965": "Kokosnuss", "1f951": "Avocado",
    "1f346": "Aubergine", "1f954": "Kartoffel", "1f955": "Karotte", "1f33d": "Mais",
    "1f336": "Chili", "1fad1": "Paprika", "1f952": "Gurke", "1f96c": "Blattgemüse",
    "1f966": "Brokkoli", "1f9c4": "Knoblauch", "1f9c5": "Zwiebel", "1f344": "Pilz",
    "1f95c": "Erdnüsse", "1fad8": "Bohnen", "1f330": "Kastanie", "1fada": "Ingwer",
    "1fadb": "Erbsen", "1f33f": "Kräuter", "1f331": "Keimling", "1f35e": "Brot",
    "1f950": "Croissant", "1f956": "Baguette", "1fad3": "Fladenbrot", "1f968": "Brezel",
    "1f96f": "Bagel", "1f95e": "Pfannkuchen", "1f9c7": "Waffel", "1f9c0": "Käse",
    "1f95a": "Ei", "1f373": "Spiegelei", "1f9c8": "Butter", "1f95b": "Milch",
    "1f37c": "Babyflasche", "1f356": "Fleisch am Knochen", "1f357": "Geflügel",
    "1f969": "Steak", "1f953": "Speck", "1f32d": "Hotdog", "1f414": "Huhn",
    "1f416": "Schwein", "1f404": "Kuh", "1f41f": "Fisch", "1f364": "Garnele",
    "1f980": "Krabbe", "1f99e": "Hummer", "1f991": "Tintenfisch", "1f9aa": "Auster",
    "1f354": "Burger", "1f35f": "Pommes", "1f355": "Pizza", "1f96a": "Sandwich",
    "1f32e": "Taco", "1f32f": "Burrito", "1f959": "Döner", "1f9c6": "Falafel",
    "1f372": "Eintopf", "1f963": "Müsli", "1f957": "Salat", "1f35a": "Reis",
    "1f35b": "Curry", "1f35c": "Nudelsuppe", "1f35d": "Spaghetti", "1f360": "Süßkartoffel",
    "1f363": "Sushi", "1f95f": "Teigtasche", "1f96b": "Konserve", "1fad9": "Glas",
    "1f9c2": "Salz", "1f36f": "Honig", "1f37f": "Popcorn", "1f36a": "Keks",
    "1f36b": "Schokolade", "1f36c": "Bonbon", "1f36d": "Lutscher", "1f36e": "Pudding",
    "1f369": "Donut", "1f370": "Kuchen", "1f382": "Torte", "1f9c1": "Muffin",
    "1f967": "Pie", "1f366": "Softeis", "1f368": "Eis", "1f9ca": "Eiswürfel",
    "2744": "Schneeflocke", "2615": "Kaffee", "1f375": "Tee", "1fad6": "Teekanne",
    "1f9c3": "Saft", "1f964": "Softdrink", "1f9cb": "Bubble Tea", "1f37a": "Bier",
    "1f377": "Wein", "1f37e": "Sekt", "1f378": "Cocktail", "1f379": "Longdrink",
    "1f943": "Schnaps", "1f9fc": "Seife", "1fae7": "Spülmittel", "1f9fd": "Schwamm",
    "1f9fb": "Papierrolle", "1f9f4": "Lotion", "1faa5": "Zahnbürste", "1fa92": "Rasierer",
    "1f9f9": "Besen", "1f5d1": "Müll", "1f9e4": "Handschuhe", "1f9fa": "Korb",
    "1f6cd": "Tüten", "1f4e6": "Paket", "1f50b": "Batterie", "1f4a1": "Glühbirne",
    "1f56f": "Kerze", "1f48a": "Medizin", "1fa79": "Pflaster", "1f9e6": "Socken",
    "1f9f8": "Spielzeug", "1f337": "Tulpe", "1f490": "Blumenstrauß", "1f415": "Hund",
    "1f408": "Katze", "1f9b4": "Knochen", "1f6d2": "Einkaufswagen",
}

OG, SN, LB, BE, MP, PT, TK, FL, KO, HH, WS, SO = CATEGORIES

# (name, additional comma-separated terms, icon, category, color)
# A term starting with "=" only matches a whole word (e.g. "=ei" does not match "Brei").
DEFAULT_FOODS = [
    # Obst
    ("Apfel", "äpfel", "1f34e", OG, "rose"),
    ("Banane", "", "1f34c", OG, "yellow"),
    ("Ananas", "", "1f34d", OG, "yellow"),
    ("Birne", "", "1f350", OG, "mint"),
    ("Zitrone", "limette", "1f34b", OG, "yellow"),
    ("Orange", "apfelsine, blutorange", "1f34a", OG, "coral"),
    ("Mandarine", "clementine, klementine", "1f34a", OG, "coral"),
    ("Grapefruit", "pampelmuse", "1f34a", OG, "coral"),
    ("Erdbeere", "", "1f353", OG, "rose"),
    ("Himbeere", "brombeere, johannisbeere, beeren", "1f353", OG, "rose"),
    ("Heidelbeere", "blaubeere", "1fad0", OG, "lilac"),
    ("Trauben", "weintraube", "1f347", OG, "lilac"),
    ("Kirsche", "", "1f352", OG, "rose"),
    ("Pfirsich", "nektarine, aprikose", "1f351", OG, "coral"),
    ("Pflaume", "zwetschge, zwetschke", "1f347", OG, "lilac"),
    ("Mango", "", "1f96d", OG, "yellow"),
    ("Melone", "honigmelone", "1f348", OG, "mint"),
    ("Wassermelone", "", "1f349", OG, "rose"),
    ("Kiwi", "", "1f95d", OG, "mint"),
    ("Kokosnuss", "kokos", "1f965", OG, "yellow"),
    ("Granatapfel", "", "1f34e", OG, "rose"),
    ("Feige", "datteln", "1f347", OG, "lilac"),
    ("Obst", "", "1f34e", OG, "rose"),
    # Gemüse
    ("Tomate", "cherrytomate, rispentomate", "1f345", OG, "coral"),
    ("Gurke", "salatgurke", "1f952", OG, "mint"),
    ("Zucchini", "", "1f952", OG, "mint"),
    ("Aubergine", "", "1f346", OG, "lilac"),
    ("Karotte", "möhre, mohrrübe, rüebli", "1f955", OG, "coral"),
    ("Paprika", "", "1fad1", OG, "mint"),
    ("Chili", "peperoni, jalapeño", "1f336", OG, "rose"),
    ("Brokkoli", "broccoli", "1f966", OG, "mint"),
    ("Blumenkohl", "", "1f966", OG, "mint"),
    ("Salat", "eisbergsalat, kopfsalat, rucola, feldsalat", "1f96c", OG, "mint"),
    ("Spinat", "mangold", "1f96c", OG, "mint"),
    ("Kohl", "weißkohl, rotkohl, wirsing, grünkohl, kohlrabi, rosenkohl", "1f96c", OG, "mint"),
    ("Lauch", "porree, frühlingszwiebel", "1f96c", OG, "mint"),
    ("Sellerie", "", "1f96c", OG, "mint"),
    ("Spargel", "", "1f96c", OG, "mint"),
    ("Kartoffel", "", "1f954", OG, "yellow"),
    ("Süßkartoffel", "", "1f360", OG, "coral"),
    ("Zwiebel", "schalotte", "1f9c5", OG, "lilac"),
    ("Knoblauch", "", "1f9c4", OG, "lilac"),
    ("Ingwer", "", "1fada", OG, "yellow"),
    ("Pilze", "champignon, pfifferling", "1f344", OG, "rose"),
    ("Avocado", "", "1f951", OG, "mint"),
    ("Maiskolben", "", "1f33d", OG, "yellow"),
    ("Kürbis", "hokkaido", "1f360", OG, "coral"),
    ("Rote Bete", "rote beete", "1f360", OG, "rose"),
    ("Radieschen", "rettich", "1f96c", OG, "rose"),
    ("Kräuter", "petersilie, basilikum, schnittlauch, dill, koriander, minze, rosmarin, thymian", "1f33f", OG, "mint"),
    ("Gemüse", "", "1f966", OG, "mint"),
    # Brot und Eier
    ("Brot", "vollkornbrot, toastbrot", "1f35e", BE, "yellow"),
    ("Toast", "", "1f35e", BE, "yellow"),
    ("Brötchen", "semmel, schrippe, weckle", "1f35e", BE, "yellow"),
    ("Baguette", "ciabatta", "1f956", BE, "yellow"),
    ("Croissant", "hörnchen", "1f950", BE, "yellow"),
    ("Brezel", "laugenbrezel, breze", "1f968", BE, "yellow"),
    ("Bagel", "", "1f96f", BE, "yellow"),
    ("Wraps", "tortilla, fladenbrot, pita", "1fad3", BE, "yellow"),
    ("Eier", "=ei", "1f95a", BE, "yellow"),
    # Milchprodukte
    ("Milch", "vollmilch, h-milch", "1f95b", MP, "blue"),
    ("Hafermilch", "sojamilch, mandelmilch", "1f95b", MP, "blue"),
    ("Joghurt", "jogurt, skyr", "1f95b", MP, "blue"),
    ("Sahne", "schlagsahne, schmand, crème fraîche, creme fraiche, saure sahne", "1f95b", MP, "blue"),
    ("Quark", "", "1f95b", MP, "blue"),
    ("Kefir", "buttermilch", "1f95b", MP, "blue"),
    ("Pudding", "", "1f36e", MP, "yellow"),
    ("Milchreis", "grießbrei, griesbrei", "1f36e", MP, "yellow"),
    ("Käse", "gouda, emmentaler, parmesan, cheddar, feta, camembert, =brie, bergkäse", "1f9c0", MP, "yellow"),
    ("Mozzarella", "burrata, ricotta", "1f9c0", MP, "yellow"),
    ("Frischkäse", "hüttenkäse", "1f9c0", MP, "yellow"),
    ("Butter", "margarine", "1f9c8", MP, "yellow"),
    # Fleischtheke
    ("Fleisch", "", "1f969", FL, "rose"),
    ("Hackfleisch", "hack, gehacktes", "1f969", FL, "rose"),
    ("Steak", "rinderfilet, rumpsteak, entrecote", "1f969", FL, "rose"),
    ("Schnitzel", "kotelett, braten", "1f356", FL, "rose"),
    ("Hähnchen", "huhn, hühnchen, hähnchenbrust, hühnerbrust, chicken, pute, truthahn", "1f357", FL, "rose"),
    ("Wurst", "würstchen, bratwurst, wiener, bockwurst", "1f32d", FL, "rose"),
    ("Salami", "aufschnitt, mortadella", "1f356", FL, "rose"),
    ("Schinken", "kochschinken, serrano, prosciutto", "1f356", FL, "rose"),
    ("Speck", "bacon, pancetta", "1f953", FL, "rose"),
    ("Fisch", "", "1f41f", FL, "blue"),
    ("Lachs", "forelle, kabeljau, dorsch, seelachs", "1f41f", FL, "blue"),
    ("Garnelen", "shrimps, krabben, scampi", "1f364", FL, "coral"),
    ("Tofu", "tempeh, seitan", "1f9c0", FL, "mint"),
    # Pasta und Tomatensachen
    ("Nudeln", "pasta, spaghetti, penne, fusilli, tagliatelle, makkaroni, spätzle, gnocchi", "1f35d", PT, "coral"),
    ("Lasagne", "lasagneplatten", "1f35d", PT, "coral"),
    ("Tomatensoße", "tomatensauce, pastasoße, pastasauce, arrabbiata", "1f345", PT, "coral"),
    ("Passierte Tomaten", "passata, stückige tomaten, gehackte tomaten", "1f345", PT, "coral"),
    ("Tomatenmark", "", "1f345", PT, "coral"),
    ("Pesto", "", "1f33f", PT, "mint"),
    ("Reis", "basmati, risotto, jasminreis", "1f35a", PT, "yellow"),
    # Konserven
    ("Konserve", "dose, dosen", "1f96b", KO, "blue"),
    ("Bohnen", "kidneybohnen, weiße bohnen, grüne bohnen", "1fad8", KO, "rose"),
    ("Kichererbsen", "linsen", "1fad8", KO, "yellow"),
    ("Erbsen", "", "1fadb", KO, "mint"),
    ("Mais", "", "1f33d", KO, "yellow"),
    ("Oliven", "", "1fad2", KO, "mint"),
    ("Thunfisch", "sardinen", "1f96b", KO, "blue"),
    ("Gewürzgurken", "essiggurken, cornichons", "1f952", KO, "mint"),
    ("Sauerkraut", "", "1fad9", KO, "mint"),
    ("Marmelade", "konfitüre, gelee", "1fad9", KO, "rose"),
    ("Honig", "", "1f36f", KO, "yellow"),
    ("Nutella", "nuss-nougat-creme, schokocreme", "1fad9", KO, "coral"),
    ("Erdnussbutter", "", "1fad9", KO, "coral"),
    ("Suppe", "brühe, fond, eintopf", "1f372", KO, "coral"),
    ("Kokosmilch", "", "1f965", KO, "yellow"),
    # TK
    ("Tiefkühlpizza", "tk-pizza, pizza", "1f355", TK, "coral"),
    ("Pommes", "pommes frites, kroketten", "1f35f", TK, "yellow"),
    ("Eis", "speiseeis, eiscreme, magnum", "1f368", TK, "rose"),
    ("Eiswürfel", "", "1f9ca", TK, "blue"),
    ("Tiefkühl", "tiefkühlgemüse, tk-gemüse, =tk, rahmspinat, fischstäbchen", "2744", TK, "blue"),
    ("Burger", "burgerpatties, patties", "1f354", TK, "coral"),
    ("Maultaschen", "teigtaschen, dumplings, tortellini, ravioli", "1f95f", TK, "yellow"),
    # Snacks und Süßes
    ("Schokolade", "schoki, kakao, nougat", "1f36b", SN, "rose"),
    ("Kekse", "keks, cookies, plätzchen, butterkeks", "1f36a", SN, "yellow"),
    ("Kuchen", "gebäck, torte", "1f370", SN, "rose"),
    ("Muffins", "", "1f9c1", SN, "rose"),
    ("Donut", "berliner, krapfen", "1f369", SN, "rose"),
    ("Chips", "flips, tortilla-chips, nachos", "1f954", SN, "yellow"),
    ("Popcorn", "", "1f37f", SN, "yellow"),
    ("Gummibärchen", "gummibären, fruchtgummi, haribo, lakritz", "1f36c", SN, "rose"),
    ("Bonbons", "bonbon, kaugummi", "1f36c", SN, "rose"),
    ("Lutscher", "lolli", "1f36d", SN, "rose"),
    ("Riegel", "müsliriegel, schokoriegel", "1f36b", SN, "coral"),
    ("Nüsse", "erdnüsse, cashew, mandeln, walnüsse, haselnüsse, pistazien, studentenfutter", "1f95c", SN, "coral"),
    ("Salzstangen", "cracker, salzbrezeln", "1f968", SN, "yellow"),
    ("Snacks", "süßigkeiten", "1f36c", SN, "rose"),
    ("Müsli", "haferflocken, cornflakes, granola, porridge, cerealien", "1f963", SN, "yellow"),
    # Limo und Bier
    ("Wasser", "mineralwasser, sprudel, sprudelwasser, stilles wasser", "1f9ca", LB, "blue"),
    ("Saft", "orangensaft, apfelsaft, multivitaminsaft, nektar", "1f9c3", LB, "blue"),
    ("Apfelschorle", "schorle, saftschorle", "1f9c3", LB, "blue"),
    ("Cola", "coke, fanta, sprite, spezi, mezzo mix", "1f964", LB, "coral"),
    ("Limonade", "limo, eistee, softdrink, energy, energydrink, red bull, =mate", "1f964", LB, "coral"),
    ("Bier", "pils, helles, weizen, weißbier, radler, alster, kölsch, =alt, =export", "1f37a", LB, "yellow"),
    ("Alkoholfreies Bier", "", "1f37a", LB, "yellow"),
    ("Kaffee", "espresso, kaffeebohnen, kaffeepads, kapseln, cappuccino", "2615", LB, "coral"),
    ("Tee", "pfefferminztee, kräutertee, früchtetee, grüner tee, schwarztee", "1f375", LB, "mint"),
    ("Kakaopulver", "trinkschokolade, kaba", "2615", LB, "coral"),
    ("Getränke", "getränk", "1f964", LB, "blue"),
    # Wein und Spirituosen
    ("Wein", "rotwein, weißwein, =rosé, roséwein", "1f377", WS, "lilac"),
    ("Sekt", "prosecco, champagner, crémant", "1f37e", WS, "lilac"),
    ("Gin", "=gin", "1f378", WS, "lilac"),
    ("Wodka", "vodka", "1f943", WS, "lilac"),
    ("Whisky", "whiskey, bourbon", "1f943", WS, "lilac"),
    ("Rum", "=rum", "1f943", WS, "lilac"),
    ("Likör", "aperol, campari, amaretto, baileys, schnaps, =korn, cognac, tequila, grappa, ouzo", "1f379", WS, "lilac"),
    # Haushaltsartikel
    ("Toilettenpapier", "klopapier, wc-papier", "1f9fb", HH, "lilac"),
    ("Küchenrolle", "küchenpapier, taschentücher, servietten", "1f9fb", HH, "lilac"),
    ("Spülmittel", "spüli, geschirrspülmittel", "1fae7", HH, "blue"),
    ("Spülmaschinentabs", "spülmaschinentabs, tabs, klarspüler, regeneriersalz, spülmaschinensalz", "1fae7", HH, "blue"),
    ("Waschmittel", "weichspüler, waschpulver, flüssigwaschmittel", "1fae7", HH, "blue"),
    ("Reiniger", "allzweckreiniger, putzmittel, wc-reiniger, glasreiniger, essigreiniger", "1fae7", HH, "blue"),
    ("Schwamm", "schwämme, spülschwamm, lappen, spültuch", "1f9fd", HH, "yellow"),
    ("Müllbeutel", "mülltüten, müllsäcke", "1f5d1", HH, "blue"),
    ("Alufolie", "frischhaltefolie, backpapier, gefrierbeutel", "1f4e6", HH, "lilac"),
    ("Seife", "handseife, flüssigseife", "1f9fc", HH, "blue"),
    ("Duschgel", "shampoo, spülung, conditioner, badezusatz", "1f9f4", HH, "blue"),
    ("Creme", "bodylotion, sonnencreme, handcreme, deo, deodorant", "1f9f4", HH, "blue"),
    ("Zahnpasta", "zahncreme, zahnbürste, zahnseide, mundspülung", "1faa5", HH, "blue"),
    ("Rasierer", "rasierklingen, rasierschaum", "1fa92", HH, "blue"),
    ("Windeln", "feuchttücher, babynahrung", "1f37c", HH, "blue"),
    ("Binden", "tampons, slipeinlagen", "1f9fb", HH, "lilac"),
    ("Batterien", "batterie", "1f50b", HH, "mint"),
    ("Glühbirne", "leuchtmittel, lampe", "1f4a1", HH, "yellow"),
    ("Kerzen", "teelichter", "1f56f", HH, "yellow"),
    ("Pflaster", "", "1fa79", HH, "coral"),
    ("Medikamente", "ibuprofen, paracetamol, aspirin, tabletten, vitamine", "1f48a", HH, "rose"),
    ("Blumen", "blumenstrauß, tulpen, rosen", "1f337", HH, "rose"),
    ("Hundefutter", "", "1f415", HH, "coral"),
    ("Katzenfutter", "katzenstreu", "1f408", HH, "coral"),
    # Sonstiges (Backen, Gewürze, Öl)
    ("Mehl", "", "1f9c2", SO, "blue"),
    ("Zucker", "puderzucker, vanillezucker, rohrzucker", "1f9c2", SO, "blue"),
    ("Salz", "", "1f9c2", SO, "blue"),
    ("Pfeffer", "gewürze, paprikapulver, curry, zimt, oregano", "1f9c2", SO, "blue"),
    ("Backpulver", "hefe, trockenhefe, natron, speisestärke", "1f9c2", SO, "blue"),
    ("Öl", "=öl, olivenöl, sonnenblumenöl, rapsöl, bratöl, speiseöl", "1fad2", SO, "mint"),
    ("Essig", "balsamico, apfelessig", "1fad2", SO, "mint"),
    ("Ketchup", "mayo, mayonnaise, senf, grillsoße, bbq-soße, sojasoße, sojasauce", "1fad9", SO, "coral"),
]

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_PLURAL_ENDINGS = ("", "n", "en", "s", "e", "er", "nen")


def normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold().translate(_UMLAUTS)
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    return " ".join(re.split(r"[^a-z0-9]+", text)).strip()


def parse_terms(name, terms):
    """Return (term, whole_word_only) pairs for a food entry."""
    result = []
    for raw in [name, *terms.split(",")]:
        raw = raw.strip()
        whole = raw.startswith("=")
        term = normalize(raw.lstrip("="))
        if term and (term, whole) not in result:
            result.append((term, whole))
    whole_terms = {term for term, whole in result if whole}
    return [(term, whole) for term, whole in result if whole or term not in whole_terms]


def _score(normalized, tokens, term, whole):
    if " " in term:
        if normalized == term:
            return 3
        return 2 if f" {term} " in f" {normalized} " else 0
    best = 0
    for token in tokens:
        if any(token == term + ending for ending in _PLURAL_ENDINGS):
            return 3
        if whole:
            continue
        if any(token.endswith(term + ending) for ending in _PLURAL_ENDINGS):
            best = max(best, 2)
        elif term in token:
            best = max(best, 1)
    return best


class Matcher:
    def __init__(self, foods):
        self.entries = [(food, parse_terms(food["name"], food["terms"])) for food in foods]

    def match(self, name):
        """Find the best food entry for an item name.

        Exact words beat word endings (the head of a German compound such as
        "Ananassaft"), which beat matches anywhere inside a word; longer terms
        win ties.
        """
        normalized = normalize(name)
        tokens = normalized.split()
        best, best_rank = None, (0, 0)
        for food, terms in self.entries:
            for term, whole in terms:
                score = _score(normalized, tokens, term, whole)
                if score and (score, len(term)) > best_rank:
                    best, best_rank = food, (score, len(term))
        return best

    def describe(self, name):
        food = self.match(name)
        if not food:
            return {"icon": FALLBACK_ICON, "category": FALLBACK_CATEGORY, "color": FALLBACK_COLOR, "food_id": None}
        return {"icon": food["icon"], "category": food["category"], "color": food["color"], "food_id": food["id"]}


def available_icons(static_dir):
    icons = {path.stem for path in (Path(static_dir) / "icons").glob("*.svg")}
    icons.discard("app-icon")
    return sorted(icons, key=lambda icon: (ICON_LABELS.get(icon, "~" + icon).casefold()))
