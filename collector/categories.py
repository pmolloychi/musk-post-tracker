"""Fixed lists. Edit here; the Claude prompt and JSON schema both read from them."""

# Topic only. Never a judgment about bigotry; that's what FLAGS are for.
CATEGORIES = [
    "Tesla",
    "SpaceX/Starlink",
    "xAI/Grok",
    "X platform",
    "Government/DOGE",
    "US politics & elections",
    "Immigration",
    "Race & ethnicity",
    "DEI policy",
    "Religion",
    "LGBTQ issues",
    "Trans issues",
    "Women & gender",
    "Media & journalists",
    "Free speech/censorship",
    "Foreign politics",
    "Science/tech",
    "Memes/personal",
    "Other",
]

# Suggestions for a human reviewer, not findings.
FLAGS = [
    "Antisemitism",
    "Racism",
    "Anti-immigrant/xenophobia",
    "Islamophobia",
    "Anti-trans",
    "Homophobia",
    "Misogyny",
    "Ableism",
]
