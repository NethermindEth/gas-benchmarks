import yaml
import requests
import os
import sys
import re

IMAGES_YAML_PATH = os.path.join(os.path.dirname(__file__), "images.yaml")

def load_images_yaml(path):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def save_images_yaml(data, path):
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

def get_latest_performance_tag(image):
    """
    Query Docker Hub API for the latest 'performance-*' tag for the given image.
    """
    if "/" not in image:
        # Not a valid Docker Hub image
        return None
    namespace, repo = image.split("/", 1)
    url = f"https://hub.docker.com/v2/repositories/{namespace}/{repo}/tags?page_size=2&name=performance"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        perf_tags = [r["name"] for r in results if r["name"].startswith("performance-")]
        if not perf_tags:
            return None
        # Sort tags by name descending (assuming lexicographical order is sufficient)
        perf_tags.sort(reverse=True)
        return perf_tags[0]
    except Exception as e:
        print(f"Error fetching tags for {image}: {e}")
        return None

def revert_performance_tags():
    images_yaml = load_images_yaml(IMAGES_YAML_PATH)
    images = images_yaml.get("images", {})
    updated = False

    for key, value in images.items():
        # Replace :performance-xxxxxxx with :performance
        m = re.match(r"^(.*:)(performance-[a-zA-Z0-9]+)$", value)
        if m:
            new_value = m.group(1) + "performance"
            if new_value != value:
                print(f"Reverting {key}: {value} -> {new_value}")
                images[key] = new_value
                updated = True

    if updated:
        save_images_yaml(images_yaml, IMAGES_YAML_PATH)
        print("images.yaml reverted to :performance tags.")
    else:
        print("No performance-* tags to revert.")

def main():
    if "--revert" in sys.argv:
        revert_performance_tags()
        return

    images_yaml = load_images_yaml(IMAGES_YAML_PATH)
    images = images_yaml.get("images", {})
    updated = False

    for key, value in images.items():
        if value.endswith(":performance"):
            image_ref = value.rsplit(":", 1)[0]
            latest_tag = get_latest_performance_tag(image_ref)
            if latest_tag:
                new_value = f"{image_ref}:{latest_tag}"
                if new_value != value:
                    print(f"Updating {key}: {value} -> {new_value}")
                    images[key] = new_value
                    updated = True
            else:
                print(f"No performance-* tag found for {image_ref}, skipping.")

    if updated:
        save_images_yaml(images_yaml, IMAGES_YAML_PATH)
        print("images.yaml updated.")
    else:
        print("No updates made to images.yaml.")

if __name__ == "__main__":
    main()
