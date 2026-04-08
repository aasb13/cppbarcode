import config
from pipeline import obfuscate_file


def main():
    obfuscate_file(config.TARGET_FILE)


if __name__ == "__main__":
    main()

