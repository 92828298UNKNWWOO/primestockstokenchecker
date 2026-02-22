import concurrent.futures
import ctypes
import json
import os
import random
import shutil
import sys
import threading
import textwrap
import time
from datetime import datetime

try:
    import msvcrt
except Exception:
    msvcrt = None

import colorama
import pystyle
import toml
from curl_cffi import requests as request

import logger

TOKENS_PATH = "data/tokens.txt"
PROXIES_PATH = "data/proxies.txt"
CONFIG_PATH = "data/config.toml"
SETTINGS_PATH = "data/settings.json"
OUTPUT_ROOT = "output"

LOCK = threading.Lock()
TOKEN_LOCK = threading.Lock()
MAX_RETRIES_PER_TOKEN = 5
REQUEST_TIMEOUT = 15


def load_lines(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def mask_token(token):
    token_only = token.split(":")[-1]
    masked_token = token_only[:20] + ".***.*****"
    return token_only, masked_token


def build_banner():
    return textwrap.dedent(f'''
        ██████╗ ██████╗ ██╗███╗   ███╗███████╗    ███████╗████████╗ ██████╗  ██████╗██╗  ██╗███████╗
        ██╔══██╗██╔══██╗██║████╗ ████║██╔════╝    ██╔════╝╚══██╔══╝██╔═══██╗██╔════╝██║ ██╔╝██╔════╝
        ██████╔╝██████╔╝██║██╔████╔██║█████╗      ███████╗   ██║   ██║   ██║██║     █████╔╝ ███████╗
        ██╔═══╝ ██╔══██╗██║██║╚██╔╝██║██╔══╝      ╚════██║   ██║   ██║   ██║██║     ██╔═██╗ ╚════██║
        ██║     ██║  ██║██║██║ ╚═╝ ██║███████╗    ███████║   ██║   ╚██████╔╝╚██████╗██║  ██╗███████║
        ╚═╝     ╚═╝  ╚═╝╚═╝╚═╝     ╚═╝╚══════╝    ╚══════╝   ╚═╝    ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝
                                {pystyle.Colorate.Color(color=pystyle.Colors.cyan, text="https://primestocks.net")}
                                {pystyle.Colorate.Color(color=pystyle.Colors.cyan, text="https://discord.gg/realprimestocks")}
    ''').strip("\n") + "\n"


def build_compact_banner():
    return textwrap.dedent(f'''
        PRIME STOCKS
        {pystyle.Colorate.Color(color=pystyle.Colors.cyan, text="https://primestocks.net")}
        {pystyle.Colorate.Color(color=pystyle.Colors.cyan, text="https://discord.gg/realprimestocks")}
    ''').strip("\n") + "\n"


def choose_banner_color():
    if hasattr(pystyle.Colors, "blue_to_white"):
        return pystyle.Colors.blue_to_white
    if hasattr(pystyle.Colors, "white_to_blue"):
        return pystyle.Colors.white_to_blue
    if hasattr(pystyle.Colors, "blue_to_cyan"):
        return pystyle.Colors.blue_to_cyan
    return pystyle.Colors.cyan


def print_banner():
    banner = build_banner()
    compact = build_compact_banner()
    color = choose_banner_color()
    banner_width = max((len(line) for line in banner.splitlines()), default=0)
    term_width = shutil.get_terminal_size(fallback=(120, 24)).columns
    if term_width < banner_width + 10:
        print(pystyle.Colorate.Vertical(text=compact, color=color))
    else:
        colored = pystyle.Colorate.Vertical(text=banner, color=color)
        print(pystyle.Center.XCenter(colored, spaces=15))


tokens = load_lines(TOKENS_PATH)
proxies = load_lines(PROXIES_PATH)

tokens = list(set(tokens))
valid = 0
invalid = 0
locked = 0
nitro = 0
flagged = 0
total = len(tokens)
current = 0
no_nitro = 0
redeemable = 0
non_redeemable = 0
done = False
config = toml.load(CONFIG_PATH)
settings = load_json(SETTINGS_PATH)

print_banner()
output_folder = f"{OUTPUT_ROOT}/{time.strftime('%Y-%m-%d %H-%M-%S')}"
logger.info("Checking Tokens", output=output_folder, total=total)
print()

ensure_dir(output_folder)
start = time.time()

class Checker:
    def __init__(self) -> None:
        self.new_session()

    def new_session(self) -> None:
        try:
            if os.name == "nt":
                self.session = request.Session(impersonate="chrome131")
            else:
                self.session = request.Session()
            self.session.headers = {
                "user-agent": 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
                'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
            }
            if not config["main"]["proxyless"] and proxies:
                proxy = random.choice(proxies).strip()
                self.session.proxies = {
                    "https": f"http://{proxy}",
                    "http": f"http://{proxy}"
                }
        except Exception:
            pass

    def retry_errors(self, e):
        err = str(e).lower()
        return (
            "connection" in err or "timeout" in err or "reset" in err or
            "refused" in err or "network" in err or "curl" in err or
            "ssl" in err or "certificate" in err or "remote host" in err or
            "closed" in err or "abruptly" in err
        )

    def _is_flagged(self, user_data, token, args):
        global flagged
        if (user_data.get("flags") or 0) & 1048576 == 1048576:
            flagged += 1
            logger.fail("Flagged", **args)
            LOCK.acquire()
            with open(f"{output_folder}/flagged.txt", "a", encoding="utf-8") as f:
                f.write(token + "\n")
            LOCK.release()
            return True
        return False

    def _resolve_type(self, user_data):
        type_name = "Unclaimed"
        if user_data.get("email") and user_data.get("verified"):
            type_name = "Email verified"
        if user_data.get("phone"):
            type_name = "Fully verified" if type_name == "Email verified" else "Phone verified"
        return type_name

    def _handle_age(self, user_data, token, type_name, args):
        try:
            uid = int(user_data.get("id") or 0)
            created_at = ((uid >> 22) + 1420070400000) / 1000
            age = (time.time() - created_at) / 86400 / 30
            age_int = f"{int(age / 12)} Years" if age > 12 else f"{int(age)} Month"
            args["age"] = age_int
            ensure_dir(f"{output_folder}/Age/{age_int}")
            LOCK.acquire()
            with open(f"{output_folder}/Age/{age_int}/{type_name}.txt", "a", encoding="utf-8") as f:
                f.write(token + "\n")
            LOCK.release()
        except Exception:
            pass

    def _handle_nitro(self, token, args):
        global nitro, no_nitro
        try:
            r2 = self.session.get("https://discord.com/api/v9/users/@me/billing/subscriptions", timeout=REQUEST_TIMEOUT)
            data = r2.json() if r2.status_code == 200 else []
            if isinstance(data, list) and data:
                for sub in data:
                    if not isinstance(sub, dict):
                        continue
                    try:
                        end = sub.get("current_period_end")
                        if not end:
                            continue
                        days_left = (time.mktime(time.strptime(end.replace("Z", "+00:00")[:26], "%Y-%m-%dT%H:%M:%S.%f")) - time.time()) / 86400
                        r3 = self.session.get("https://discord.com/api/v9/users/@me/guilds/premium/subscription-slots", timeout=REQUEST_TIMEOUT)
                        slots = r3.json() if r3.status_code == 200 else []
                        available = sum(1 for s in (slots if isinstance(slots, list) else []) if isinstance(s, dict) and s.get("cooldown_ends_at") is None)
                        month = "1 Month" if days_left <= 31 else "3 Month"
                        args["boost"] = available
                        args["nitro"] = f"{month} [{days_left:.0f}d]"
                        nitro += 1
                        if available == 0:
                            boost_label = "0-boosts"
                        elif available == 1:
                            boost_label = "1-boost"
                        else:
                            boost_label = f"{available}-boosts"
                        boost_path = f"{output_folder}/Boosts/{boost_label}"
                        ensure_dir(boost_path)
                        LOCK.acquire()
                        with open(f"{boost_path}/tokens.txt", "a", encoding="utf-8") as f:
                            f.write(token + "\n")
                        LOCK.release()
                        cooldown = sub.get("cooldown_ends_at")
                        if cooldown is None:
                            path = f"{output_folder}/Nitro/No Cooldown/{month}/{days_left:.0f} days"
                            ensure_dir(path)
                            LOCK.acquire()
                            with open(f"{path}/{available} boosts.txt", "a", encoding="utf-8") as f:
                                f.write(token + "\n")
                            LOCK.release()
                            args["cooldown"] = "No Cooldown"
                        else:
                            dt_obj = datetime.fromisoformat(str(cooldown).replace("Z", "+00:00"))
                            cd = f"{dt_obj.day}d {dt_obj.hour}hrs"
                            path = f"{output_folder}/Nitro/Cooldown/{month}/{days_left:.0f} days"
                            ensure_dir(path)
                            LOCK.acquire()
                            with open(f"{path}/{cd}.txt", "a", encoding="utf-8") as f:
                                f.write(token + "\n")
                            LOCK.release()
                            args["cooldown"] = cd
                    except Exception:
                        pass
            else:
                LOCK.acquire()
                args["boost"] = 0
                args["nitro"] = "No Nitro"
                with open(f"{output_folder}/No Nitro.txt", "a", encoding="utf-8") as f:
                    f.write(token + "\n")
                LOCK.release()
                no_nitro += 1
        except Exception:
            LOCK.acquire()
            try:
                args["boost"] = 0
                args["nitro"] = "No Nitro"
                with open(f"{output_folder}/No Nitro.txt", "a", encoding="utf-8") as f:
                    f.write(token + "\n")
                no_nitro += 1
            except Exception:
                pass
            LOCK.release()

    def _handle_redeemable(self, token, args):
        global redeemable, non_redeemable
        try:
            r2 = self.session.get("https://discord.com/api/v9/users/@me/billing/subscriptions?include_inactive=true", timeout=REQUEST_TIMEOUT)
            if r2.status_code == 200 and isinstance(r2.text, str):
                if "[]" in r2.text:
                    args["redeemable"] = "Redeemable"
                    with open(f"{output_folder}/Redeemable.txt", "a", encoding="utf-8") as f:
                        f.write(token + "\n")
                    redeemable += 1
                else:
                    args["redeemable"] = "Non Redeemable"
                    with open(f"{output_folder}/Non Redeemable.txt", "a", encoding="utf-8") as f:
                        f.write(token + "\n")
                    non_redeemable += 1
        except Exception:
            pass

    def check(self) -> None:
        global current, total, valid, locked, nitro, invalid, flagged, no_nitro, redeemable, non_redeemable
        while True:
            token = None
            try:
                TOKEN_LOCK.acquire()
                if not tokens:
                    TOKEN_LOCK.release()
                    break
                token = tokens.pop().strip()
                TOKEN_LOCK.release()
            except Exception as e:
                if TOKEN_LOCK.locked():
                    try:
                        TOKEN_LOCK.release()
                    except Exception:
                        pass
                logger.fail("Error", error=e)
                continue

            token_only, masked_token = mask_token(token)
            args = {"token": masked_token}

            for attempt in range(MAX_RETRIES_PER_TOKEN):
                try:
                    self.session.headers["authorization"] = token_only
                    r = self.session.get("https://discord.com/api/v9/users/@me/guilds", timeout=REQUEST_TIMEOUT)

                    if r.status_code == 429:
                        retry_after = 1.5
                        try:
                            if "retry-after" in r.headers:
                                retry_after = max(float(r.headers.get("retry-after") or retry_after), 0.5)
                            else:
                                body = r.json()
                                if isinstance(body, dict) and "retry_after" in body:
                                    retry_after = max(float(body.get("retry_after") or retry_after), 0.5)
                        except Exception:
                            pass
                        logger.fail("Rate limited", token=masked_token, wait=f"{retry_after:.1f}s")
                        time.sleep(retry_after + random.uniform(0, 0.4))
                        TOKEN_LOCK.acquire()
                        tokens.append(token)
                        TOKEN_LOCK.release()
                        break
                    current += 1

                    if r.status_code == 401:
                        invalid += 1
                        logger.fail("Invalid", token=masked_token)
                        LOCK.acquire()
                        with open(f"{output_folder}/invalid.txt", "a", encoding="utf-8") as f:
                            f.write(token + "\n")
                        LOCK.release()
                        break
                    if r.status_code == 403:
                        locked += 1
                        logger.fail("Locked", token=masked_token)
                        LOCK.acquire()
                        with open(f"{output_folder}/locked.txt", "a", encoding="utf-8") as f:
                            f.write(token + "\n")
                        LOCK.release()
                        break

                    if r.status_code != 200:
                        raise Exception(f"Unexpected status {r.status_code}")

                    r = self.session.get("https://discord.com/api/v9/users/@me", timeout=REQUEST_TIMEOUT)
                    if r.status_code != 200:
                        raise Exception("users @me failed")
                    try:
                        user_data = r.json()
                    except Exception:
                        user_data = {}
                    if not isinstance(user_data, dict):
                        user_data = {}

                    if settings["flagged"]:
                        if self._is_flagged(user_data, token, args):
                            break

                    if settings["type"]:
                        type_name = self._resolve_type(user_data)
                    else:
                        type_name = "Valid"
                    args["type"] = type_name

                    if settings["age"]:
                        self._handle_age(user_data, token, type_name, args)

                    if settings["nitro"]:
                        self._handle_nitro(token, args)

                    if settings["redeemable"]:
                        self._handle_redeemable(token, args)

                    valid += 1
                    logger.success("Valid", **args)
                    LOCK.acquire()
                    with open(f"{output_folder}/{type_name}.txt", "a", encoding="utf-8") as f:
                        f.write(token + "\n")
                    with open(f"{output_folder}/Valid.txt", "a", encoding="utf-8") as f:
                        f.write(token + "\n")
                    LOCK.release()
                    break

                except Exception as e:
                    if not self.retry_errors(e) or attempt >= MAX_RETRIES_PER_TOKEN - 1:
                        TOKEN_LOCK.acquire()
                        tokens.append(token)
                        TOKEN_LOCK.release()
                        logger.fail("Error", **args, error=e)
                        break
                    self.new_session()
                    time.sleep(0.2 + random.uniform(0, 0.3))
                    continue


def update_title():
    while not done:
        try:
            time.sleep(0.1)
            elapsed = max(time.time() - start, 0.001)
            pct = (current / total * 100) if total else 0
            cps = current / elapsed if elapsed else 0
            title = (
                "Prime Stocks Checker | Valid: {valid} | Invalid: {invalid} | "
                "Locked: {locked} | Remaining: {remaining} | Checked: {pct:.2f}% | CPS: {cps:.2f}"
            ).format(
                valid=valid,
                invalid=invalid,
                locked=locked,
                remaining=len(tokens),
                pct=pct,
                cps=cps,
            )
            if os.name == "nt":
                ctypes.windll.kernel32.SetConsoleTitleW(title)
            else:
                sys.stdout.write(f"\33]0;{title}\7")
                sys.stdout.flush()
        except Exception:
            pass


def wait_for_enter():
    if os.name == "nt" and msvcrt:
        while True:
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b'\r':
                    break
    else:
        input()

    logger.info("Exiting in 3 seconds...")
    time.sleep(3)

if __name__ == "__main__":
    colorama.init()
    time.sleep(0.1)
    update = threading.Thread(target=update_title)
    update.start()

    with concurrent.futures.ThreadPoolExecutor(max_workers=config["main"]["threads"]) as executor:
        for i in range(config["main"]["threads"]):
            executor.submit(Checker().check)

    done = True
    update.join()
    print()
    logger.info(f"Checked {current} tokens in {time.gmtime(time.time()-start).tm_min} minutes and {time.gmtime(time.time()-start).tm_sec} seconds")
    logger.info("Finished checking tokens:", Checked=current, Valid=valid, Invalid=invalid, Nitro=nitro, Locked=locked, Flagged=flagged, Redeemable=redeemable, Non_Redeemable=non_redeemable)
    logger.info("Press Enter to exit.")
    wait_for_enter()
