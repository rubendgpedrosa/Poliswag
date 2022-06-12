def prepare_environment(env):
    if env == "prod":
        return ".env"
    elif env == "dev":
        return "dev.env"
    else:
        print("Invalid environment, usage: python3 main.py (dev|prod)")
        quit()
