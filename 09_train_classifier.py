"""Train a cheap local text classifier for lobbying issues.

Uses TF-IDF + Logistic Regression on the 79 Senate LDA issue codes.
This replaces expensive LLM calls for basic classification.
"""

import json
import pickle
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score

from db import get_db, query_to_dicts

MODEL_DIR = Path(__file__).parent / "models"
MODEL_DIR.mkdir(exist_ok=True)

# LDA Issue Code descriptions (from Senate)
LDA_CODES = {
    "ACC": "Accounting", "ADV": "Advertising", "AER": "Aerospace",
    "AGR": "Agriculture", "ALC": "Alcohol & Drug Abuse", "ANI": "Animals",
    "APP": "Apparel/Textiles", "ART": "Arts/Entertainment", "AUT": "Automotive",
    "AVI": "Aviation", "BAN": "Banking", "BEV": "Beverage Industry",
    "BNK": "Bankruptcy", "BUD": "Budget/Appropriations", "CAW": "Clean Air & Water",
    "CDT": "Commodities", "CHM": "Chemicals/Toxics", "CIV": "Civil Rights",
    "COM": "Communications/Broadcasting", "CON": "Constitution", "CPI": "Computer Industry",
    "CPT": "Copyright/Patent/Trademark", "CSP": "Consumer Issues/Safety", "DEF": "Defense",
    "DIS": "Disaster Planning", "DOC": "District of Columbia", "ECN": "Economics",
    "EDU": "Education", "ENG": "Energy/Nuclear", "ENV": "Environment",
    "FAM": "Family/Abortion", "FIN": "Financial Institutions", "FIR": "Firearms",
    "FOO": "Food Industry", "FOR": "Foreign Relations", "FUE": "Fuel/Gas/Oil",
    "GAM": "Gaming/Gambling", "GOV": "Government Issues", "HCR": "Health Issues",
    "HOM": "Homeland Security", "HOU": "Housing", "IMM": "Immigration",
    "IND": "Indian/Native American", "INS": "Insurance", "INT": "Intelligence",
    "LAW": "Law Enforcement", "LBR": "Labor Issues", "MAN": "Manufacturing",
    "MAR": "Marine/Fishing", "MED": "Media/Publishing", "MIA": "Medical/Disease Research",
    "MMM": "Medicare/Medicaid", "MON": "Minting/Money", "NAT": "Natural Resources",
    "PHA": "Pharmacy", "POS": "Postal", "RES": "Real Estate",
    "RET": "Retirement", "ROD": "Roads/Highway", "RRR": "Railroads",
    "SCI": "Science/Technology", "SMB": "Small Business", "SPO": "Sports/Athletics",
    "TAR": "Tariff/Imports", "TAX": "Taxation", "TEC": "Telecommunications",
    "TOB": "Tobacco", "TOR": "Torts", "TOU": "Travel/Tourism",
    "TRA": "Transportation", "TRD": "Trade", "TRU": "Trucking/Shipping",
    "URB": "Urban Development", "UNM": "Unemployment", "UTI": "Utilities",
    "VET": "Veterans", "WAS": "Waste/Hazardous", "WEL": "Welfare"
}


def load_training_data(min_examples: int = 50) -> tuple[list, list]:
    """Load text-label pairs from database."""
    sql = """
        SELECT a.description, a.issue_code
        FROM activities a
        WHERE a.description IS NOT NULL
          AND LENGTH(a.description) > 20
          AND a.issue_code IS NOT NULL
          AND a.issue_code IN (
              SELECT issue_code FROM activities
              WHERE issue_code IS NOT NULL
              GROUP BY issue_code HAVING COUNT(*) >= ?
          )
    """
    with get_db() as conn:
        rows = query_to_dicts(conn, sql, (min_examples,))

    texts = [r["description"] for r in rows]
    labels = [r["issue_code"] for r in rows]
    return texts, labels


def train_classifier(texts: list, labels: list, test_size: float = 0.2):
    """Train TF-IDF + Logistic Regression classifier."""
    print(f"Training on {len(texts)} examples...")

    # Split data
    X_train, X_test, y_train, y_test = train_test_split(
        texts, labels, test_size=test_size, random_state=42, stratify=labels
    )

    # TF-IDF vectorization
    vectorizer = TfidfVectorizer(
        max_features=10000,
        ngram_range=(1, 2),
        min_df=5,
        max_df=0.95,
        stop_words="english"
    )

    X_train_tfidf = vectorizer.fit_transform(X_train)
    X_test_tfidf = vectorizer.transform(X_test)

    print(f"Vocabulary size: {len(vectorizer.vocabulary_)}")

    # Train logistic regression (fast, interpretable)
    classifier = LogisticRegression(
        max_iter=1000,
        n_jobs=-1,
        class_weight="balanced",
        C=1.0
    )

    classifier.fit(X_train_tfidf, y_train)

    # Evaluate
    y_pred = classifier.predict(X_test_tfidf)
    accuracy = accuracy_score(y_test, y_pred)

    print(f"\nTest Accuracy: {accuracy:.3f}")
    print("\nClassification Report (top classes):")
    print(classification_report(y_test, y_pred, zero_division=0))

    return vectorizer, classifier, accuracy


def save_model(vectorizer, classifier, accuracy: float):
    """Save trained model to disk."""
    model_path = MODEL_DIR / "issue_classifier.pkl"

    model_data = {
        "vectorizer": vectorizer,
        "classifier": classifier,
        "accuracy": accuracy,
        "lda_codes": LDA_CODES
    }

    with open(model_path, "wb") as f:
        pickle.dump(model_data, f)

    print(f"\nModel saved to {model_path}")
    return model_path


def load_model():
    """Load trained model from disk."""
    model_path = MODEL_DIR / "issue_classifier.pkl"

    with open(model_path, "rb") as f:
        model_data = pickle.load(f)

    return model_data


class LocalClassifier:
    """Wrapper for easy inference."""

    def __init__(self):
        data = load_model()
        self.vectorizer = data["vectorizer"]
        self.classifier = data["classifier"]
        self.lda_codes = data["lda_codes"]

    def predict(self, text: str) -> tuple[str, float, str]:
        """Predict issue code for text.
        Returns (code, confidence, description)."""
        X = self.vectorizer.transform([text])

        # Get prediction and probability
        code = self.classifier.predict(X)[0]
        probs = self.classifier.predict_proba(X)[0]
        confidence = probs.max()

        description = self.lda_codes.get(code, "Unknown")
        return code, confidence, description

    def predict_batch(self, texts: list) -> list[tuple[str, float, str]]:
        """Predict for multiple texts."""
        X = self.vectorizer.transform(texts)
        codes = self.classifier.predict(X)
        probs = self.classifier.predict_proba(X)

        results = []
        for code, prob in zip(codes, probs):
            confidence = prob.max()
            description = self.lda_codes.get(code, "Unknown")
            results.append((code, confidence, description))

        return results

    def predict_top_k(self, text: str, k: int = 3) -> list[tuple[str, float, str]]:
        """Get top-k predictions with probabilities."""
        X = self.vectorizer.transform([text])
        probs = self.classifier.predict_proba(X)[0]

        # Get top-k indices
        top_indices = np.argsort(probs)[-k:][::-1]
        classes = self.classifier.classes_

        results = []
        for idx in top_indices:
            code = classes[idx]
            confidence = probs[idx]
            description = self.lda_codes.get(code, "Unknown")
            results.append((code, confidence, description))

        return results


def main():
    """Train and save the classifier."""
    print("Loading training data...")
    texts, labels = load_training_data(min_examples=50)

    print(f"Loaded {len(texts)} examples with {len(set(labels))} unique labels")

    vectorizer, classifier, accuracy = train_classifier(texts, labels)
    save_model(vectorizer, classifier, accuracy)

    # Test inference
    print("\n" + "="*50)
    print("Testing inference...")
    print("="*50)

    clf = LocalClassifier()

    test_texts = [
        "Lobbying on tariffs and trade policy with China",
        "Healthcare reform and Medicare expansion",
        "Defense budget appropriations for military equipment",
        "Climate change and renewable energy tax credits",
        "Banking regulation and financial services oversight"
    ]

    for text in test_texts:
        results = clf.predict_top_k(text, k=3)
        print(f"\nText: {text[:60]}...")
        for code, conf, desc in results:
            print(f"  {code} ({desc}): {conf:.2%}")


if __name__ == "__main__":
    main()
