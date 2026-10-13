from Code.load_env import load_env
import send2trash
import os

def clear_folder(*paths: str) -> None:
    for path in paths:
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            if os.path.isfile(item_path) and item != '.gitkeep':
                try:
                    send2trash.send2trash(item_path)
                except Exception as ex:
                    print(f'Ошибка при удалении: {item_path} - {ex}\n')

if __name__ == "__main__":
    load_env()
    clear_folder(
        os.getenv('OUTPUT_')
    )