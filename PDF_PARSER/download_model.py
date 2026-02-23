from huggingface_hub import snapshot_download

def download_model():
    print("Downloading Deepseek model...")
    snapshot_download(
        repo_id="deepseek-ai/DeepSeek-R1-Distill-Llama-8B",
        local_dir="models/deepseek-r1-8b",
        ignore_patterns="*.bin.index.json"
    )
    print("Model downloaded successfully!")

if __name__ == "__main__":
    download_model()
