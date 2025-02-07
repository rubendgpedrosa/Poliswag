# Poliswag

Poliswag is a Python-based Discord Bot that offers seamless integration with the [Pokémon Scanner](https://pogoleiria.pt), made available by [PoGoLeiria](https://discord.gg/pASCYbp). This bot enables easy management and preparation of data for users of the Pokémon Scanner, providing hassle-free access to its data.

### Key Features:
1. Fail safes to ensure the smooth functioning of the [Pokémon Scanner](https://pogoleiria.pt).
2. Remote control through [Discord](https://discord.gg/pASCYbp) text channels, enabling the management of notifications sent by the Pokémon Scanner.
3. Automatic rescans for special quests or events.
4. Quest listing for Discord users.
5. Other random features coming soon!

## Installation

### 1. Clone the repository

Clone the repository to your local machine.

```bash
git clone https://github.com/rubendgpedrosa/Poliswag
cd Poliswag
```

### 2. Install dependencies

Poliswag requires Python >= 3.5.3. You can install the required dependencies by running:

```bash
pip install -r requirements.txt
```

### 3. Set up Black and pre-commit hooks

Poliswag uses Black for automatic code formatting and pre-commit hooks for maintaining consistent code style.

```bash
pip install black pre-commit
```
And then run the following command.

```bash
pre-commit install
```

This command will configure the pre-commit hook so that Black will automatically format your code before each commit.

## Usage

To run the bot in development or production mode:

```bash
python3 main.py (dev|prod)
```

## License
[MIT](https://choosealicense.com/licenses/mit/)
