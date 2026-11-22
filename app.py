import discord
from discord.ext import commands
import os
import sqlite3
import base64
import hashlib
import threading
import websocket
import random
import urllib
import json
import asyncio
import hmac
import urllib.request
from pathlib import Path

from websockets.asyncio.server import serve

# load_dotenv()
TWITCH_TOKEN = os.getenv("TWITCH_TOKEN")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

obs_client = None
main_loop = None
WS_SECRET = os.getenv("WS_SECRET")

stop_event = threading.Event()
twitch_socket = None

database_path = os.path.join(os.path.dirname(__file__), "gacha.db")

def initialize_database():
    conn = sqlite3.connect(database_path)
    
    conn.executescript("""
        
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS User (
    User_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    Twitch_ID TEXT NOT NULL UNIQUE,
    Discord_ID TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS Rarity (
    Rarity_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    Name TEXT NOT NULL UNIQUE,
    Weight INTEGER NOT NULL CHECK (Weight > 0)
);

CREATE TABLE IF NOT EXISTS Gacha_Item (
    Gacha_Item_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    Rarity_ID INTEGER NOT NULL, 
    Name TEXT NOT NULL UNIQUE,
    Image_Path TEXT NOT NULL,
    
    FOREIGN KEY (Rarity_ID)
        REFERENCES Rarity(Rarity_ID)
);

CREATE TABLE IF NOT EXISTS Gacha_Pull (
    Gacha_Pull_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    User_ID INTEGER NOT NULL,
    Gacha_Item_ID INTEGER NOT NULL,
    Date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    Twitch_Redemption_ID TEXT UNIQUE,

    FOREIGN KEY (User_ID)
        REFERENCES User(User_ID),

    FOREIGN KEY (Gacha_Item_ID)
        REFERENCES Gacha_Item(Gacha_Item_ID)
);

CREATE TABLE IF NOT EXISTS Inventory_Item (
    Inventory_ID INTEGER PRIMARY KEY AUTOINCREMENT,
    User_ID INTEGER NOT NULL,
    Gacha_Item_ID INTEGER NOT NULL,
    Quantity INTEGER NOT NULL DEFAULT 1
        CHECK (Quantity > 0),

    FOREIGN KEY (User_ID)
        REFERENCES User(User_ID),

    FOREIGN KEY (Gacha_Item_ID)
        REFERENCES Gacha_Item(Gacha_Item_ID),

    UNIQUE (User_ID, Gacha_Item_ID)
);
    """)

    conn.commit()
    conn.close()

initialize_database()

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def on_ready(self):
        await self.tree.sync()

        print(f'Logged on as {self.user}!')
        # get_leaderboard()

    async def on_message(self, message):
        print(f'Message from {message.author}: {message.content}')
        # if "🫃" in message.content:
        #     await self.timeout_user(message.author)

        await self.process_commands(message)

bot = MyBot()    


def run_query(query):
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()
    cursor.execute(query)
    result = cursor.fetchall()
    # for row in result:
    #     print(row)
    conn.close()
    
    return result

def get_rarity_ID(rarity):
    conn = sqlite3.connect(database_path)
    cursor = conn.cursor()
    
    id = cursor.execute("SELECT Rarity_ID FROM Rarity WHERE Name = ?", (rarity, ))
    id = cursor.fetchone()[0]
    conn.close()
    
    return id

def get_all_gacha_items():
    gacha_items = run_query(f"SELECT * FROM Gacha_Item")
    return gacha_items

def process_gacha_pull(twitch_user_id, redemption_id):
    conn = sqlite3.connect(database_path)
        
    gacha_items = run_query("""
        SELECT g.Name, r.Weight
        FROM Gacha_Item as g
        JOIN Rarity as r
        on g.Rarity_ID = r.Rarity_ID
        """)
    
    print (gacha_items)
    
    items = []
    weights =[]
    
    for item in gacha_items:
        items.append(item[0])
        weights.append(item[1])
    
    pulled_item = random.choices(items, weights=weights, k=1)[0]
    print(pulled_item)
    
    gacha_id = run_query(f"SELECT Gacha_Item_ID FROM Gacha_Item WHERE Name = '{pulled_item}'")[0][0]
    user_id = create_user(twitch_user_id)
    
    try: 
        conn.execute("""INSERT INTO Gacha_Pull VALUES (NULL, ?, ?, date('now'), ?)""", (user_id, gacha_id, redemption_id))
        print("This worked yippee yippee yippee")
    except Exception as e:
        print(e)
    
    conn.commit()
    conn.close()
    
    return pulled_item

def pull_history(twitch_user_id):
    pull_history = run_query(f"SELECT * FROM Gacha_Pull")
    
    return pull_history

def check_if_user_exists(twitch_user_id):
    return bool(run_query(f"SELECT EXISTS(SELECT 1 FROM User WHERE Twitch_ID = {twitch_user_id} LIMIT 1)")[0][0])

def create_user(twitch_user_id): 
    if check_if_user_exists(twitch_user_id): 
        return run_query(f"SELECT User_ID FROM User WHERE Twitch_ID = {twitch_user_id}")[0][0]
    
    conn = sqlite3.connect(database_path)
    conn.execute("""INSERT INTO User VALUES (NULL, ?, NULL)""", (twitch_user_id,))
    conn.commit()
    conn.close()
    
    return run_query(f"SELECT User_ID FROM User WHERE Twitch_ID = {twitch_user_id}")[0][0]

async def send_gacha_result(username, pulled_item, filename):
    if obs_client is None:
        print("OBS is not connected!")
        return

    await obs_client.send(json.dumps({
        "type": "gacha",
        "username": username,
        "item": pulled_item, #Name of item, not id
        "item_filename": filename
    }))

@bot.tree.command(
    name="add_gacha_item",
    description="What do you think this does? It adds a gacha item, duh"
)
@commands.has_permissions(administrator=True)
async def add_gacha_item(interaction: discord.Interaction,
    name: str,
    image: discord.Attachment,
    rarity: str
    ):
    
    conn = sqlite3.connect(database_path)
    
    rarity_ID = get_rarity_ID(rarity)
    try:
        SAVE_DIR = Path(__file__).resolve().parent / "Gacha Drops"
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        
        file_path = SAVE_DIR / f"{image.id}_{image.filename}"

        await image.save(str(file_path))

        conn.execute("""INSERT INTO Gacha_Item VALUES (NULL, ?, ?, ?)""", (rarity_ID, name, str(file_path)))
        await interaction.response.send_message("Something happened!")
    except sqlite3.IntegrityError as e:
        print("oh noooo")
        await interaction.response.send_message(e)
    except Exception as e:
        print("oh noooo")
        await interaction.response.send_message(e)
    conn.commit()
    conn.close


@bot.tree.command(
    name="edit_gacha_item",
    description="What do you think this does? It edits a gacha item, duh"
)
@commands.has_permissions(administrator=True)
async def edit_gacha_item(interaction: discord.Interaction,
    name: str,
    image: discord.Attachment,
    rarity: str
    ):
    
    conn = sqlite3.connect(database_path)
    
    rarity_ID = get_rarity_ID(rarity)
    try: 
        SAVE_DIR = "C:/Python/Twitch Gacha/Gacha Drops/"
        file_path = SAVE_DIR + f"{image.id}_{image.filename}"

        await image.save(file_path)
        
        conn.execute("""UPDATE Gacha_Item SET Rarity_ID = ?, Image_Path = ? WHERE Name = ?""", (rarity_ID, file_path, name))
        await interaction.response.send_message("Something happened!")
    except sqlite3.IntegrityError as e:
        await interaction.response.send_message("Integrity Error!")
    conn.commit()
    conn.close

@bot.tree.command(
    name="view_gacha_items",
    description="This lets you view rarities!"
)
@commands.has_permissions(administrator=True)
async def view_gacha_items(interaction: discord.Interaction):
    conn = sqlite3.connect(database_path)
    try:
        message = "Name - Rarity\n" + str(run_query("SELECT g.Name, r.Name FROM Gacha_Item as g JOIN Rarity as r ON g.Rarity_ID = r.Rarity_ID"))
        await interaction.response.send_message(message)
    except:
        await interaction.response.send_message("Something went wrong, teehee")
    conn.close()

@bot.tree.command(
    name="add_rarity",
    description="This adds rarities!"
)
@commands.has_permissions(administrator=True)
async def add_rarity(interaction: discord.Interaction,
    name: str,
    weight: int
    ):
    conn = sqlite3.connect(database_path)
    
    try:
        conn.execute("""INSERT INTO Rarity VALUES (NULL, ?, ?)""", (name, weight))
        conn.commit()
        await interaction.response.send_message("Something happened!")

    except sqlite3.IntegrityError as e:
        if str(e) == "UNIQUE constraint failed: Rarity.Name":
            await interaction.response.send_message(f"Oh no! '{name}' already exists as a rarity!")
        else:
            await interaction.response.send_message(f"Cloudy does not know what happened here. This is apparently the error: \n{e}")
    
    conn.close()
    
@bot.tree.command(
    name="view_rarities",
    description="This lets you view rarities!"
)
@commands.has_permissions(administrator=True)
async def view_rarities(interaction: discord.Interaction):
    conn = sqlite3.connect(database_path)
    try:
        message = "Rarity ID - Name - Weight\n" + str(run_query("SELECT * FROM Rarity ORDER BY Rarity_ID"))
        await interaction.response.send_message(message)
    except:
        await interaction.response.send_message("Something went wrong, teehee")
    conn.close()

@bot.tree.command(
    name="edit_rarity",
    description="This lets you view rarities!"
)
@commands.has_permissions(administrator=True)
async def edit_rarity(interaction: discord.Interaction, name: str, weight: int):
    conn = sqlite3.connect(database_path)
    try:
        conn.execute("""UPDATE Rarity SET Weight = ? WHERE Name = ?""", (weight, name))
        conn.commit()
        await interaction.response.send_message("Something happened! (lowkey this Cloudy guy is too lazy to write these messages)")

    except sqlite3.ProgrammingError as e:
        await interaction.response.send_message(e)

    except:
        await interaction.response.send_message("Something went wrong")

    conn.close()

@bot.tree.command(
    name="delete_rarity",
    description="This lets you view rarities!"
)
@commands.has_permissions(administrator=True)
async def delete_rarity(interaction: discord.Interaction, name: str):
    conn = sqlite3.connect(database_path)
    try:
        conn.execute("""DELETE FROM Rarity WHERE Name = ?""", (name, ))
        conn.commit()
        await interaction.response.send_message("Something happened! (lowkey this Cloudy guy is too lazy to write these messages)")

    except sqlite3.ProgrammingError as e:
        await interaction.response.send_message(e)
        
    except:
        await interaction.response.send_message("Something went wrong")

    conn.close()

def validate_token():
    request = urllib.request.Request(
        "https://id.twitch.tv/oauth2/validate",
        headers={"Authorization": f"OAuth {TWITCH_TOKEN}"}
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.load(response)

    if "channel:read:redemptions" not in data["scopes"]:
        raise ValueError("Missing channel:read:redemptions scope")

    if not data.get("user_id"):
        raise ValueError("Expected a broadcaster user token")

    return data

def subscribe_to_redemptions(session_id, auth):
    subscription = {
        "type": "channel.channel_points_custom_reward_redemption.add",
        "version": "1",
        "condition": {
            "broadcaster_user_id": auth["user_id"]
        },
        "transport": {
            "method": "websocket",
            "session_id": session_id
        }
    }

    request = urllib.request.Request(
        "https://api.twitch.tv/helix/eventsub/subscriptions",
        data=json.dumps(subscription).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {TWITCH_TOKEN}",
            "Client-Id": auth["client_id"],
            "Content-Type": "application/json"
        },
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=8) as response:
        if response.status != 202:
            raise RuntimeError("Subscription failed")

    print("log", "Subscribed to Twitch redemptions!")

def listen_to_twitch():
    global twitch_socket

    try:
        auth = validate_token()
        print(f"Authenticated as {auth['login']}")
    except Exception as error:
        print(error)
        return

    def on_message(ws, message):
        try:
            data = json.loads(message)
            message_type = data["metadata"]["message_type"]

            if message_type == "session_welcome":
                session_id = data["payload"]["session"]["id"]
                subscribe_to_redemptions(session_id, auth)

            elif message_type == "notification":
                event = data["payload"]["event"]
                future = asyncio.run_coroutine_threadsafe(
                    on_redemption(event),
                    main_loop
                )

                def report_error(f):
                    try:
                        f.result()
                    except Exception as error:
                        print("Gacha error:", error)

                future.add_done_callback(report_error)

            elif message_type == "session_reconnect":
                # Basic reconnect; a production version should
                # transfer to Twitch's supplied reconnect URL.
                ws.close()

            elif message_type == "revocation":
                print("Subscription revoked")

        except Exception as error:
            print(str(error))

    def on_error(ws, error):
        print(str(error))

    while not stop_event.is_set():
        try:
            twitch_socket = websocket.WebSocketApp(
                "wss://eventsub.wss.twitch.tv/ws",
                on_message=on_message,
                on_error=on_error
            )

            twitch_socket.run_forever()

        except Exception as error:
            print(str(error))

        if stop_event.wait(5):
            break

async def on_redemption(event):
    username = event["user_name"]
    reward = event["reward"]["title"]
    print(f"{username} redeemed {reward}")

    if reward == "Gacha test":
        pulled_item = process_gacha_pull(event["user_id"], event["id"])
        print(run_query("SELECT * FROM Gacha_Item"))
        item_filename = run_query(f"SELECT Image_Path From Gacha_Item WHERE Name = '{pulled_item}'")[0][0]
        
        await send_gacha_result(username, pulled_item, item_filename)

async def send_all_gacha_images(ws):
    conn = sqlite3.connect(database_path)
    
    images = conn.execute("""
        SELECT Gacha_Item_ID, Image_Path
        FROM Gacha_Item
    """).fetchall()
    
    for item_id, image_path in images:
        try:
            path = Path(image_path)
            
            image_bytes = await asyncio.to_thread(path.read_bytes)
            
            await ws.send(json.dumps({
                "type": "gacha_image",
                "image_filename": path.name,
                "image_data": base64.b64encode(
                    image_bytes
                ).decode("ascii")
            }))
            
        except Exception as error:
            print(f"Failed to send {image_path}: {error}")
    
    await ws.send(json.dumps({
        "type": "sync_complete",
    }))

async def handle_obs(websocket):
    global obs_client

    try:
        message = json.loads(
            await asyncio.wait_for(
                websocket.recv(),
                timeout=5
            )
        )

        if not hmac.compare_digest(
            str(message.get("token", "")),
            WS_SECRET
        ):
            await websocket.close(
                code=1008,
                reason="Unauthorized"
            )
            return

        obs_client = websocket
        print("OBS connected!")

        async for message in websocket:
            data = json.loads(message)

            if data.get("type") == "sync_images":
                await send_all_gacha_images(websocket)

    except Exception as error:
        print("OBS connection error:", error)

    finally:
        if obs_client is websocket:
            obs_client = None

        print("OBS disconnected!")

async def main():
    global main_loop

    main_loop = asyncio.get_running_loop()

    async with bot:
        async with serve(handle_obs, "0.0.0.0", 25447):
            print("WebSocket server started!")

            twitch_thread = threading.Thread(
                target=listen_to_twitch,
                daemon=True
            )

            twitch_thread.start()

            try:
                await bot.start(DISCORD_TOKEN)

            finally:
                stop_event.set()

                if twitch_socket is not None:
                    twitch_socket.close()

if __name__ == "__main__":
    asyncio.run(main())