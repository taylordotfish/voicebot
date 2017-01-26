#!/usr/bin/env python3
# Copyright (C) 2017 taylor.fish <contact@taylor.fish>
#
# This file is part of voicebot.
#
# voicebot is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# As an additional permission under GNU AGPL version 3 section 7, if a
# modified version of this Program responds to the message "help" in an
# IRC query with an opportunity to receive the Corresponding Source, it
# satisfies the requirement to "prominently offer" such an opportunity.
# All other requirements in the first paragraph of section 13 must still
# be met.
#
# voicebot is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with voicebot.  If not, see <http://www.gnu.org/licenses/>.
"""
Usage:
  voicebot [options] <host> <port> <nickname> <channel>
  voicebot -h | --help | --version

Options:
  -t --time <seconds>    How long to wait before devoicing inactive users.
                         [default: 86400]. (86400 seconds == 1 day)
  -f --force-id          Force users to be logged in with NickServ.
  -x --prefixes <chars>  Allow users with any of these prefixes to operate
                         voicebot [default: @].
  -p --password          Read an IRC password from standard input.
  -P --passfile <file>   Read an IRC password from the specified file.
  -S --sasl <account>    Use SASL authentication with the specified account.
  -s --ssl               Use SSL/TLS to connect to the IRC server.
  -v --verbose           Display communication with the IRC server.
"""
from docopt import docopt
from pyrcb2 import IRCBot, Event, astdio, IDict, IDefaultDict
from getpass import getpass
import asyncio
import json
import os
import sys
import time

__version__ = "0.1.1"

# If modified, update this URL to point to the modified version.
SOURCE_URL = "https://github.com/taylordotfish/voicebot"
HELP_MESSAGE = """\
Source: {0} (AGPLv3 or later)
""".format(SOURCE_URL)

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
NICKNAMES_PATH = os.path.join(SCRIPT_DIR, "nicknames")
ACCOUNTS_PATH = os.path.join(SCRIPT_DIR, "accounts")
DATA_PATH = os.path.join(SCRIPT_DIR, "voicebot-data")

NONE = (None,)  # Sentinel

COMMAND_LOOP_HELP = """\
Commands:
  add-nickname <nickname>
  add-account <account>
  remove-nickname <nickname>
  remove-account <account>
  list-nicknames
  list-accounts
""".rstrip("\n")

ARG_COUNT = {
    "add-nickname": 1,
    "add-account": 1,
    "remove-nickname": 1,
    "remove-account": 1,
    "list-nicknames": 0,
    "list-accounts": 0,
}


class Voicebot:
    def __init__(self, channel, duration, force_id, prefixes, verbose):
        self.channel = channel
        self.duration = duration
        self.force_id = force_id
        self.prefixes = frozenset(prefixes)
        self.nicknames = IDict()
        self.accounts = IDict()
        self.nickname_last_message_times = IDefaultDict(time.time)
        self.account_last_message_times = IDefaultDict(time.time)
        self.invalid_cmd_counts = IDict()

        self.bot = IRCBot(log_communication=verbose)
        self.bot.track_known_id_statuses = self.force_id
        self.bot.load_events(self)
        self.load()

    def load(self):
        self.nicknames.update((n, None) for n in read_lines(NICKNAMES_PATH))
        self.accounts.update((a, None) for a in read_lines(ACCOUNTS_PATH))
        lines = read_lines(DATA_PATH)
        data = json.loads("\n".join(lines)) if lines else [{}, {}]
        self.nickname_last_message_times.update(data[0])
        self.account_last_message_times.update(data[1])
        self.filter_times()

    def save(self):
        write_lines(NICKNAMES_PATH, list(self.nicknames))
        write_lines(ACCOUNTS_PATH, list(self.accounts))
        self.filter_times()
        with open(DATA_PATH, "w") as f:
            json.dump([
                self.nickname_last_message_times,
                self.account_last_message_times,
            ], f)

    def filter_times(self):
        for nickname in self.nickname_last_message_times:
            if nickname not in self.nicknames:
                del self.nickname_last_message_times[nickname]
        for account in self.account_last_message_times:
            if account not in self.accounts:
                del self.account_last_message_times[account]

    def start(self, host, port, ssl, nick, password, sasl_account):
        self.bot.schedule_coroutine(self.command_loop())
        self.bot.call_coroutine(self.start_async(
            host, port, ssl, nick, password, sasl_account,
        ))

    async def start_async(self, host, port, ssl, nick, password, sasl_account):
        await self.bot.connect(host, port, ssl=ssl)
        if sasl_account:
            await self.bot.sasl_auth(sasl_account, password)
            password = None
        await self.bot.register(nick, password=password)
        result = await self.bot.join(self.channel)
        if not result.success:
            raise result.to_exception("Could not join channel")
        interval = min(self.duration / 4, 60)
        self.bot.schedule_coroutine(self.devoice_loop(interval))
        await self.bot.listen()

    @Event.privmsg
    async def on_privmsg(self, sender, channel, message):
        op_cmd = False
        if sender in self.bot.users[self.channel]:
            if self.bot.users[self.channel][sender].prefixes & self.prefixes:
                op_cmd = self.on_op_message(sender, channel, message)
        if channel is None:
            if not op_cmd:
                self.on_query(sender, message)
            return
        await self.check_voice(sender)

    def on_query(self, sender, message):
        if message.lower() == "help":
            for line in HELP_MESSAGE.splitlines():
                self.bot.privmsg(sender, line)
        else:
            if self.invalid_cmd_allowed(sender):
                self.bot.privmsg(sender, 'Type "help" for help.')
            return
        self.valid_cmd_received(sender)

    def on_op_message(self, sender, channel, message):
        if channel is not None:
            if not message.startswith(self.bot.nickname + ": "):
                return False
            message = (message.split(None, 1)[1:] or [""])[0]
        try:
            command, *args = message.split()
        except ValueError:
            return False
        commands = [
            "add-nickname", "add-account", "remove-nickname", "remove-account"]
        if not (command in commands and len(args) == ARG_COUNT[command]):
            return False
        response = self.handle_command(command, *args)
        if channel is not None:
            response = sender + ": " + response
            self.valid_cmd_received(sender)
        self.bot.privmsg(channel or sender, response)
        return True

    @Event.nick
    async def on_nick(self, sender, nickname):
        await self.refresh_voice_status(nickname)

    @Event.join
    async def on_join(self, sender, channel):
        await self.refresh_voice_status(sender, update_times=False)

    @Event.command("ACCOUNT")
    async def on_account(self, sender, account):
        await self.refresh_voice_status(sender, update_times=False)

    async def refresh_voice_status(self, nickname, update_times=True):
        user = self.bot.users[self.channel][nickname]
        if user.has_prefix("+"):
            await self.check_devoice(nickname)
            return
        await self.check_voice(nickname, update_times)

    async def check_voice(self, nickname, update_times=True):
        account, end_early = await self.get_account(nickname)
        if account is NONE:
            return
        if not (nickname in self.nicknames or account in self.accounts):
            return
        if self.force_id and (await self.get_id_status(nickname)) != 3:
            return
        if update_times and nickname in self.nicknames:
            self.nickname_last_message_times[nickname] = time.time()
        if update_times and account in self.accounts:
            self.account_last_message_times[account] = time.time()
        if not update_times:
            msg_time = self.get_last_message_time(nickname, account)
            if time.time() - msg_time > self.duration:
                return
        if not self.bot.users[self.channel][nickname].has_prefix("+"):
            print("Voicing {}...".format(nickname))
            self.bot.send_command("MODE", self.channel, "+v", nickname)

    async def check_devoice(self, nickname):
        account, end_early = await self.get_account(nickname)
        if account is NONE or end_early:
            return
        devoice = not (nickname in self.nicknames or account in self.accounts)
        if self.force_id and not devoice:
            devoice = (await self.get_id_status(nickname) != 3)
        if not devoice:
            msg_time = self.get_last_message_time(nickname, account)
            devoice = time.time() - msg_time > self.duration
        if devoice:
            print("Devoicing {}...".format(nickname))
            self.bot.send_command("MODE", self.channel, "-v", nickname)

    async def get_account(self, nickname):
        will_call_known_event = (
            self.bot.is_tracking_known_accounts and
            not self.bot.is_account_synced(nickname))
        result = await self.bot.get_account(nickname)
        if not result.success:
            stderr("Could not get account:", result.to_exception())
            return NONE, False
        return result.value, will_call_known_event

    async def get_id_status(self, nickname):
        result = await self.bot.get_id_status(nickname)
        if not result.success:
            stderr("Could not get ID status:", result.to_exception())
            return None
        return result.value

    def get_users(self, voiced):
        users = []
        for user in self.bot.users[self.channel].values():
            if user.has_prefix("+") == bool(voiced):
                users.append(user)
        return users

    def get_last_message_time(self, nickname, account):
        times = []
        if nickname in self.nicknames:
            times.append(self.nickname_last_message_times[nickname])
        if account in self.accounts:
            times.append(self.account_last_message_times[account])
        if not times:
            raise ValueError("User is not managed by voicebot.")
        return max(times)

    def invalid_cmd_allowed(self, sender, max_invalid=10):
        count, _ = self.invalid_cmd_counts.get(sender, (0, None))
        self.invalid_cmd_counts[sender] = (count + 1, time.time())
        self.invalid_cmd_collect_garbage()
        return count <= max_invalid

    def valid_cmd_received(self, sender):
        self.invalid_cmd_counts.pop(sender, None)

    def invalid_cmd_collect_garbage(self, timeout=120):
        now = time.time()
        while self.invalid_cmd_counts:
            _, last_time = next(iter(self.invalid_cmd_counts.values()))
            if now - last_time < timeout:
                break
            self.invalid_cmd_counts.popitem(last=False)

    async def devoice_loop(self, interval=60):
        while True:
            await asyncio.sleep(interval)
            if self.channel in self.bot.channels:
                await self.bot.gather(*(
                    self.check_devoice(user) for user in self.get_users(True)
                ))

    def handle_command(self, command, *args):
        if command == "add-nickname":
            self.nicknames[args[0]] = None
            return "Nickname added."
        if command == "add-account":
            self.accounts[args[0]] = None
            return "Account added."
        if command == "remove-nickname":
            self.nicknames.pop(args[0], None)
            self.nickname_last_message_times.pop(args[0], None)
            return "Nickname removed."
        if command == "remove-account":
            self.accounts.pop(args[0], None)
            self.account_last_message_times.pop(args[0], None)
            return "Account removed."
        if command == "list-nicknames":
            return "\n".join(self.nicknames)
        if command == "list-accounts":
            return "\n".join(self.accounts)
        return None

    async def command_loop(self):
        while True:
            try:
                args = (await astdio.input()).split()
            except EOFError:
                break
            text = COMMAND_LOOP_HELP
            if args and len(args) == ARG_COUNT.get(args[0], -1) + 1:
                text = self.handle_command(*args)
            if text:
                await astdio.print(text, file=sys.stderr)


def read_lines(path):
    try:
        with open(path) as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def write_lines(path, lines):
    if not os.path.exists(path) and not lines:
        return
    with open(path, "w") as f:
        for line in lines:
            print(line, file=f)


def stderr(*args, **kwargs):
    kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)


def main(argv):
    args = docopt(__doc__, argv=argv[1:], version=__version__)
    password = None
    if args["--passfile"] is not None:
        with open(args["--passfile"]) as f:
            password = f.read().strip("\r\n")
    elif args["--password"] or args["--sasl"] is not None:
        print("Password: ", end="", file=sys.stderr, flush=True)
        password = getpass("") if sys.stdin.isatty() else input()
        if not sys.stdin.isatty():
            print("Received password.", file=sys.stderr)

    voicebot = Voicebot(
        args["<channel>"], int(args["--time"]), args["--force-id"],
        args["--prefixes"], args["--verbose"],
    )

    try:
        voicebot.start(
            args["<host>"], int(args["<port>"]), args["--ssl"],
            args["<nickname>"], password, args["--sasl"],
        )
    finally:
        voicebot.save()

if __name__ == "__main__":
    main(sys.argv)
