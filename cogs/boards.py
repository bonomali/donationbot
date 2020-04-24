import asyncio
import asyncpg
import coc
import discord
import itertools
import io
import logging
import math
import time

from collections import namedtuple
from datetime import datetime
from discord.ext import commands, tasks
from PIL import Image, UnidentifiedImageError

from cogs.utils.db_objects import DatabaseMessage, BoardPlayer, BoardConfig
from cogs.utils.formatters import CLYTable, get_render_type
from cogs.utils.images import DonationBoardImage, TrophyBoardImage
from cogs.utils import checks


log = logging.getLogger(__name__)

MockPlayer = namedtuple('MockPlayer', 'clan name')
mock = MockPlayer('Unknown', 'Unknown')

REFRESH_EMOJI = discord.PartialEmoji(name="refresh", id=694395354841350254, animated=False)
LEFT_EMOJI = discord.PartialEmoji(name="\N{BLACK LEFT-POINTING TRIANGLE}\ufe0f", id=None, animated=False)    # [:arrow_left:]
RIGHT_EMOJI = discord.PartialEmoji(name="\N{BLACK RIGHT-POINTING TRIANGLE}\ufe0f", id=None, animated=False)   # [:arrow_right:]
PERCENTAGE_EMOJI = discord.PartialEmoji(name="percent", id=694463772135260169, animated=False)
GAIN_EMOJI = discord.PartialEmoji(name="gain", id=696280508933472256, animated=False)
LAST_ONLINE_EMOJI = discord.PartialEmoji(name="lastonline", id=696292732599271434, animated=False)
HISTORICAL_EMOJI = discord.PartialEmoji(name="historical", id=694812540290465832, animated=False)

GLOBAL_BOARDS_CHANNEL_ID = 663683345108172830


class DonationBoard(commands.Cog):
    """Contains all DonationBoard Configurations.
    """
    def __init__(self, bot):
        self.bot = bot

        self.clan_updates = []

        self._to_be_deleted = set()

        self._batch_lock = asyncio.Lock(loop=bot.loop)
        self._data_batch = {}

        self.update_board_loops.add_exception_type(asyncpg.PostgresConnectionError, coc.ClashOfClansException)
        self.update_board_loops.start()

        self.update_global_board.add_exception_type(asyncpg.PostgresConnectionError, coc.ClashOfClansException)
        self.update_global_board.start()

        self.tags_to_update = set()
        self.last_updated_tags = {}
        self.last_updated_channels = {}
        self._board_channels = []

    def cog_unload(self):
        self.update_board_loops.cancel()
        self.update_global_board.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        self.webhooks = itertools.cycle(n for n in await self.bot.get_guild(691779140059267084).webhooks())

    @property
    def board_channels(self):
        if not self._board_channels:
            self._board_channels = itertools.cycle(n for n in self.bot.get_guild(691779140059267084).text_channels)
        return self._board_channels

    @tasks.loop(seconds=60.0)
    async def update_board_loops(self):
        await self.bot.wait_until_ready()
        clan_tags = self.tags_to_update.copy()
        self.tags_to_update.clear()

        self.last_updated_tags.update(**{n: datetime.utcnow() for n in clan_tags})

        query = """SELECT DISTINCT boards.channel_id, boards.type
                    FROM boards
                    INNER JOIN clans
                    ON clans.channel_id = boards.channel_id
                    WHERE clans.clan_tag = ANY($1::TEXT[])
                """
        fetch = await self.bot.pool.fetch(query, clan_tags)

        for n in fetch:
            try:
                await self.update_board(n['channel_id'], n['type'])
                self.last_updated_channels[n['channel_id']] = datetime.utcnow()
            except:
                log.exception(f"old board failed...\nChannel ID: {n['channel_id']}")

    @tasks.loop(hours=1)
    async def update_global_board(self):
        query = "SELECT * FROM boards WHERE channel_id = $1"
        fetch = await self.bot.pool.fetch(query, GLOBAL_BOARDS_CHANNEL_ID)
        for row in fetch:
            config = BoardConfig(bot=self.bot, record=row)
            await self.new_donationboard_updater(config, 0, season_offset=0, reset=True)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if not isinstance(channel, discord.TextChannel):
            return

        query = "DELETE FROM messages WHERE channel_id = $1;"
        query2 = "DELETE FROM boards WHERE channel_id = $1"
        query3 = "DELETE FROM logs WHERE channel_id = $1"
        query4 = "DELETE FROM clans WHERE channel_id = $1"

        for q in (query, query2, query3, query4):
            await self.bot.pool.execute(q, channel.id)

        self.bot.utils.board_config.invalidate(self.bot.utils, channel.id)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        config = await self.bot.utils.board_config(payload.channel_id)

        if not config:
            return
        if config.channel_id != payload.channel_id:
            return
        if payload.message_id in self._to_be_deleted:
            self._to_be_deleted.discard(payload.message_id)
            return

        self.bot.utils.get_message.invalidate(self.bot.utils, payload.message_id)

        message = await self.safe_delete(message_id=payload.message_id, delete_message=False)
        if message:
            await self.new_board_message(self.bot.get_channel(payload.channel_id), config.type)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        config = await self.bot.utils.board_config(payload.channel_id)

        if not config:
            return
        if config.channel_id != payload.channel_id:
            return

        for n in payload.message_ids:
            if n in self._to_be_deleted:
                self._to_be_deleted.discard(n)
                continue

            self.bot.utils.get_message.invalidate(self, n)

            message = await self.safe_delete(message_id=n, delete_message=False)
            if message:
                await self.new_board_message(self.bot.get_channel(payload.channel_id), config.type)

    async def new_board_message(self, channel, board_type):
        if not channel:
            return

        try:
            new_msg = await channel.send('Placeholder')
        except (discord.NotFound, discord.Forbidden):
            return

        query = "INSERT INTO messages (guild_id, message_id, channel_id) VALUES ($1, $2, $3)"
        await self.bot.pool.execute(query, new_msg.guild.id, new_msg.id, new_msg.channel.id)

        event_config = await self.bot.utils.event_config(channel.id)
        if event_config:
            await self.bot.background.remove_event_msg(event_config.id, channel, board_type)
            await self.bot.background.new_event_message(event_config, channel.guild.id, channel.id, board_type)

        return new_msg

    async def safe_delete(self, message_id, delete_message=True):
        query = "DELETE FROM messages WHERE message_id = $1 RETURNING id, guild_id, message_id, channel_id"
        fetch = await self.bot.pool.fetchrow(query, message_id)
        if not fetch:
            return None

        message = DatabaseMessage(bot=self.bot, record=fetch)
        if not delete_message:
            return message

        self._to_be_deleted.add(message_id)
        m = await message.get_message()
        if not m:
            return

        await m.delete()

    async def get_board_messages(self, config, number_of_msg=None):
        if not (config.channel or config.toggle):
            return

        fetch = await config.messages()

        messages = [await n.get_message() for n in fetch if await n.get_message()]
        size_of = len(messages)

        if not number_of_msg or size_of == number_of_msg:
            return messages

        if size_of > number_of_msg:
            for n in messages[number_of_msg:]:
                await self.safe_delete(n.id)
            return messages[:number_of_msg]

        if not config.channel:
            return

        for _ in range(number_of_msg - size_of):
            m = await self.new_board_message(config.channel, config.type)
            if not m:
                return
            messages.append(m)

        return messages

    async def get_top_players(self, players, board_type, sort_by, in_event, season_id=None):
        season_id = season_id or await self.bot.seasonconfig.get_season_id()
        if board_type == 'donation':
            column_1 = 'donations'
            column_2 = 'received'
            sort_by = 'donations' if sort_by == 'donation' else sort_by
        elif board_type == 'trophy':
            column_1 = 'trophies'
            column_2 = 'trophies - start_trophies'
            sort_by = column_2 if sort_by == 'gain' else column_1
        elif board_type == 'last_online':
            column_1 = 'now() - last_updated'
            column_2 = 'id'
            sort_by = 'last_updated'
        else:
            return

        # this should be ok since columns can only be a choice of 4 defined names
        if in_event:
            query = f"""SELECT player_tag, {column_1}, {column_2} 
                        FROM eventplayers 
                        WHERE player_tag=ANY($1::TEXT[])
                        AND live=true
                        ORDER BY {sort_by} DESC NULLS LAST 
                        LIMIT 100;
                    """
            fetch = await self.bot.pool.fetch(query, [n.tag for n in players])

        else:
            query = f"""SELECT player_tag, {column_1}, {column_2}
                        FROM players 
                        WHERE player_tag=ANY($1::TEXT[])
                        AND season_id=$2
                        ORDER BY {sort_by} DESC NULLS LAST
                        LIMIT 100;
                    """
            fetch = await self.bot.pool.fetch(query, [n.tag for n in players], season_id)
        return fetch

    async def update_board(self, channel_id=None, board_type=None, message_id=None):
        if message_id:
            config = await self.bot.utils.board_config(message_id)
        else:
            config = await self.bot.utils.board_config_from_channel(channel_id, board_type)

        if not config:
            return
        if not config.toggle:
            return
        if not config.channel:
            return

        if config.type in ["donation", "trophy"] and not config.in_event:
            return await self.new_donationboard_updater(config)

        if config.in_event:
            query = """SELECT DISTINCT clan_tag FROM clans WHERE channel_id=$1 AND in_event=$2"""
            fetch = await self.bot.pool.fetch(query, channel_id, config.in_event)
        else:
            query = "SELECT DISTINCT clan_tag FROM clans WHERE channel_id=$1"
            fetch = await self.bot.pool.fetch(query, channel_id)

        clans = await self.bot.coc.get_clans((n[0] for n in fetch)).flatten()
        if not clans:
            return

        players = []
        for n in clans:
            players.extend(p for p in n.itermembers)

        try:
            top_players = await self.get_top_players(players, config.type, config.sort_by, config.in_event)
        except:
            log.exception(
                f"{clans} channelid: {channel_id}, guildid: {config.guild_id},"
                f" sort: {config.sort_by}, event: {config.in_event}, type: {config.type}"
            )
            return
        players = {n.tag: n for n in players if n.tag in set(x['player_tag'] for x in top_players)}

        message_count = math.ceil(len(top_players) / 20)

        messages = await self.get_board_messages(config, number_of_msg=message_count)
        if not messages:
            return

        for i, v in enumerate(messages):
            player_data = top_players[i*20:(i+1)*20]
            table = CLYTable()

            for x, y in enumerate(player_data):
                index = i*20 + x
                if config.render == 2:
                    table.add_row([index,
                                   y[1],
                                   players.get(y['player_tag'], mock).name.replace("`", "")])
                else:
                    table.add_row([index,
                                   y[1],
                                   y[2],
                                   players.get(y['player_tag'], mock).name.replace("`", "")])

            render = get_render_type(config, table)
            fmt = render()

            e = discord.Embed(colour=self.get_colour(config.type, config.in_event),
                              description=fmt,
                              timestamp=datetime.utcnow()
                              )
            e.set_author(name=f'Event in Progress!' if config.in_event
                              else config.title,
                         icon_url=config.icon_url or 'https://cdn.discordapp.com/'
                                                     'emojis/592028799768592405.png?v=1')
            e.set_footer(text='Last Updated')
            await v.edit(embed=e, content=None)

    @staticmethod
    def get_next_per_page(page_no, config_per_page):
        if config_per_page == 0:
            lookup = {
                1: 15,
                2: 15,
                3: 20,
                4: 25,
                5: 25
            }
            if page_no > 5:
                return 50
            return lookup[page_no]

        return config_per_page

    async def new_donationboard_updater(self, config, add_pages=0, season_offset=0, reset=False):
        if config.channel_id == GLOBAL_BOARDS_CHANNEL_ID and not (add_pages or season_offset or reset):
            return

        donationboard = config.type == 'donation'
        start = time.perf_counter()
        message = await self.bot.utils.get_message(config.channel, config.message_id)
        if not message:
            try:
                message = await config.channel.send("Placeholder.... do not delete me!")
            except (discord.Forbidden, discord.NotFound):
                await self.bot.pool.execute("UPDATE boards SET toggle = FALSE WHERE channel_id = $1", config.channel_id)
                return

            await message.add_reaction(REFRESH_EMOJI)
            await message.add_reaction(LEFT_EMOJI)
            await message.add_reaction(RIGHT_EMOJI)
            if donationboard:
                await message.add_reaction(PERCENTAGE_EMOJI)
            else:
                await message.add_reaction(GAIN_EMOJI)

            await message.add_reaction(LAST_ONLINE_EMOJI)
            await message.add_reaction(HISTORICAL_EMOJI)
            await self.bot.pool.execute("UPDATE boards SET message_id = $1 WHERE channel_id = $2 AND type = $3", message.id, config.channel_id, config.type)

        try:
            page = int(message.embeds[0]._footer['text'].split(";")[0].split(" ")[1])
            season_id = int(message.embeds[0]._footer['text'].split(";")[1].split(" ")[1])
        except (AttributeError, KeyError, ValueError, IndexError):
            page = 1
            season_id = await self.bot.seasonconfig.get_season_id()

        if page + add_pages < 1:
            return  # don't bother about page 0's

        offset = 0

        if reset:
            offset = 0
            page = 1
            season_id = await self.bot.seasonconfig.get_season_id()
        else:
            for i in range(1, page + add_pages):
                offset += self.get_next_per_page(i, config.per_page)
            season_id += season_offset

        if season_id < 1:
            season_id = await self.bot.seasonconfig.get_season_id()
        if offset < 0:
            offset = 0

        if config.channel_id == GLOBAL_BOARDS_CHANNEL_ID:
            query = f"""SELECT DISTINCT player_name,
                                        donations,
                                        received,
                                        trophies,
                                        now() - last_updated AS "last_online",
                                        donations / NULLIF(received, 0) AS "ratio",
                                        trophies - start_trophies AS "gain"
                       FROM players
                       INNER JOIN clans
                       ON clans.clan_tag = players.clan_tag
                       WHERE season_id = $1
                       ORDER BY {'donations' if config.sort_by == 'donation' else config.sort_by} DESC
                       NULLS LAST
                       LIMIT $2
                       OFFSET $3
                    """
            fetch = await self.bot.pool.fetch(
                query,
                season_id,
                self.get_next_per_page(page + add_pages, config.per_page),
                offset
            )
        else:
            query = f"""SELECT DISTINCT player_name,
                                        donations,
                                        received,
                                        trophies,
                                        now() - last_updated AS "last_online",
                                        donations / NULLIF(received, 0) AS "ratio",
                                        trophies - start_trophies AS "gain"
                       FROM players
                       INNER JOIN clans
                       ON clans.clan_tag = players.clan_tag
                       WHERE clans.channel_id = $1
                       AND season_id = $2
                       ORDER BY {'donations' if config.sort_by == 'donation' else config.sort_by} DESC
                       NULLS LAST
                       LIMIT $3
                       OFFSET $4
                    """
            fetch = await self.bot.pool.fetch(
                query,
                config.channel_id,
                season_id,
                self.get_next_per_page(page + add_pages, config.per_page),
                offset
            )

        players = [BoardPlayer(n[0], n[1], n[2], n[3], n[4], n[6], i + offset + 1) for i, n in enumerate(fetch)]

        if not players:
            return  # they scrolled too far

        if config.icon_url:
            try:
                icon_bytes = await self.bot.http.get_from_cdn(config.icon_url)
                icon = Image.open(io.BytesIO(icon_bytes)).resize((180, 180))
            except (discord.Forbidden, UnidentifiedImageError):
                await self.bot.pool.execute("UPDATE boards SET icon_url = NULL WHERE message_id = $1", message.id)
                icon = None
        else:
            icon = None

        fetch = await self.bot.pool.fetchrow("SELECT start, finish FROM seasons WHERE id = $1", season_id)
        season_start, season_finish = fetch[0].strftime('%d-%b-%Y'), fetch[1].strftime('%d-%b-%Y')

        if donationboard:
            image = DonationBoardImage(config.title, icon, season_start, season_finish)
        else:
            image = TrophyBoardImage(config.title, icon, season_start, season_finish)

        image.add_players(players)
        render = image.render()

        logged_board_message = await next(self.webhooks).send(
            f"Perf: {(time.perf_counter() - start) * 1000}ms\n"
            f"Channel: {config.channel_id}\n"
            f"Guild: {config.guild_id}",
            file=discord.File(render, f'{config.type}board.png'),
            wait=True
        )
        await self.bot.background.log_message_send(config.message_id, config.channel_id,  config.guild_id, config.type + 'board')

        e = discord.Embed(colour=discord.Colour.blue() if donationboard else discord.Colour.green())
        e.set_image(url=logged_board_message.attachments[0].url)
        e.set_footer(text=f"Page {page + add_pages};Season {season_id};").timestamp = datetime.utcnow()
        await message.edit(content=None, embed=e)

    @staticmethod
    def get_colour(board_type, in_event):
        if board_type == 'donation':
            if in_event:
                return discord.Colour.gold()
            return discord.Colour.blue()
        if in_event:
            return discord.Colour.purple()
        return discord.Colour.green()

    @commands.command(hidden=True)
    @commands.is_owner()
    async def forceboard(self, ctx, message_id: int = None):
        await self.update_board(message_id=message_id)
        await ctx.confirm()

    @commands.command(hidden=True)
    @commands.is_owner()
    async def testdonationboard(self, ctx):
        q = "SELECT DISTINCT player_name, donations, received, now() - last_updated FROM players INNER JOIN clans ON players.clan_tag  = clans.clan_tag WHERE clans.guild_id = $1 AND season_id = 9 ORDER BY donations DESC LIMIT 50;"
        fetch = await ctx.db.fetch(q, ctx.guild.id)
        players = [BoardPlayer(n[0], n[1], n[2], n[3], i + 1) for i, n in enumerate(fetch)]
        s = time.perf_counter()
        im = DonationBoardImage(None)
        im.add_players(players)
        r = im.render()
        m = await next(self.webhooks).send(f"{(time.perf_counter() - s) * 1000}ms", file=discord.File(r, 'test.jpg'), wait=True)
        e = discord.Embed()
        e.set_image(url=m.attachments[0].url)
        e.set_footer(text=f"Page 1. Use the reactions to change pages.")
        await ctx.send(embed=e)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self.reaction_action(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self.reaction_action(payload)

    async def reaction_action(self, payload):
        await self.bot.wait_until_ready()
        if payload.user_id == self.bot.user.id:
            return
        if payload.emoji not in (REFRESH_EMOJI, LEFT_EMOJI, RIGHT_EMOJI, PERCENTAGE_EMOJI, GAIN_EMOJI, LAST_ONLINE_EMOJI, HISTORICAL_EMOJI):
            return

        message = await self.bot.utils.get_message(self.bot.get_channel(payload.channel_id), payload.message_id)
        if not message:
            return
        if not message.author.id == self.bot.user.id:
            return

        query = "SELECT * FROM boards WHERE message_id = $1"
        fetch = await self.bot.pool.fetchrow(query, payload.message_id)
        if not fetch:
            return

        hard_reset = False
        offset = 0
        season_offset = 0

        if payload.emoji == RIGHT_EMOJI:
            offset = 1

        elif payload.emoji == LEFT_EMOJI:
            offset = -1

        elif payload.emoji == REFRESH_EMOJI:
            original_sort = 'donations' if fetch['type'] == 'donation' else 'trophies'
            query = "UPDATE boards SET sort_by = $1 WHERE message_id = $2 RETURNING *"
            fetch = await self.bot.pool.fetchrow(query, original_sort, payload.message_id)
            hard_reset = True

        elif payload.emoji == PERCENTAGE_EMOJI:
            query = "UPDATE boards SET sort_by = 'ratio' WHERE message_id = $1 RETURNING *"
            fetch = await self.bot.pool.fetchrow(query, payload.message_id)

        elif payload.emoji == GAIN_EMOJI:
            query = "UPDATE boards SET sort_by = 'gain' WHERE message_id = $1 RETURNING *"
            fetch = await self.bot.pool.fetchrow(query, payload.message_id)

        elif payload.emoji == LAST_ONLINE_EMOJI:
            query = "UPDATE boards SET sort_by = 'last_online ASC, player_name' WHERE message_id = $1 RETURNING *"
            fetch = await self.bot.pool.fetchrow(query, payload.message_id)

        elif payload.emoji == HISTORICAL_EMOJI:
            season_offset = -1

        config = BoardConfig(bot=self.bot, record=fetch)
        await self.new_donationboard_updater(config, offset, season_offset=season_offset, reset=hard_reset)


def setup(bot):
    bot.add_cog(DonationBoard(bot))
