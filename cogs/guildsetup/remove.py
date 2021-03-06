import discord
import asyncio
import datetime
import re
import coc
import logging
import typing

from discord.ext import commands
from cogs.utils.checks import requires_config, manage_guild
from cogs.utils.formatters import CLYTable
from cogs.utils.converters import PlayerConverter, TextChannel
from cogs.utils import checks

log = logging.getLogger(__name__)

url_validator = re.compile(r"^(?:http(s)?://)?[\w.-]+(?:.[\w.-]+)+[\w\-_~:/?#[\]@!$&'()*+,;=.]+"
                           r"(.jpg|.jpeg|.png|.gif)+[\w\-_~:/?#[\]@!$&'()*+,;=.]*$")


class Remove(commands.Cog):
    """Remove clans, players, boards, logs and more."""
    def __init__(self, bot):
        self.bot = bot

    @commands.group(invoke_without_subcommands=True)
    async def remove(self, ctx):
        """[Group] Allows the user to remove a variety of features from the bot."""
        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @remove.command(name='clan')
    @checks.manage_guild()
    async def remove_clan(self, ctx, channel: typing.Optional[discord.TextChannel], clan_tag: str):
        """Unlink a clan from a channel.

        **Parameters**
        :key: A discord channel (#mention). If not present, it will use the channel you're currently in.
        :key: A clan tag

        **Format**
        :information_source: `+remove clan #CLAN_TAG`
        :information_source: `+remove clan #CHANNEL #CLAN_TAG`

        **Example**
        :white_check_mark: `+remove clan #P0LYJC8C`
        :information_source: `+remove clan #donationlog #CLAN_TAG`

        **Required Permissions**
        :warning: Manage Server
        """
        channel = channel or ctx.channel
        clan_tag = coc.utils.correct_tag(clan_tag)
        query = "DELETE FROM clans WHERE clan_tag = $1 AND channel_id = $2"
        await ctx.db.execute(query, clan_tag, channel.id)

        try:
            clan = await self.bot.coc.get_clan(clan_tag)
            self.bot.dispatch('clan_unclaim', ctx, clan)
        except coc.NotFound:
            return await ctx.send('Clan not found.')
        await ctx.confirm()

    @remove.command(name='player', hidden=True)
    @commands.is_owner()
    async def remove_player(self, ctx, *, player: PlayerConverter):
        """Manually remove a clash account from the database.

        **Parameters**
        :key: Player name OR tag.

        **Format**
        :information_source: `+remove player #PLAYER_TAG`
        :information_source: `+remove player PLAYER NAME`

        **Example**
        :white_check_mark: `+remove player #P0LYJC8C`
        :white_check_mark: `+remove player mathsman`
        """
        query = "DELETE FROM players WHERE player_tag = $1"
        result = await ctx.db.execute(query, player.tag, ctx.guild.id)
        if result[:-1] == 0:
            return await ctx.send(f'{player.name}({player.tag}) was not found in the database.')
        await ctx.confirm()

    @remove.command(name='discord')
    async def remove_discord(self, ctx, *, player: PlayerConverter):
        """Unlink a clash account from your discord account.

        If you have not claimed the account, you must have `Manage Server` permissions.

        **Parameters**
        :key: Player name OR tag.

        **Format**
        :information_source: `+remove discord #PLAYER_TAG`
        :information_source: `+remove discord PLAYER NAME`

        **Example**
        :white_check_mark: `+remove discord #P0LYJC8C`
        :white_check_mark: `+remove discord mathsman`
        """
        season_id = await self.bot.seasonconfig.get_season_id()
        if ctx.channel.permissions_for(ctx.author).manage_guild \
                or await self.bot.is_owner(ctx.author):
            query = "UPDATE players SET user_id = NULL WHERE player_tag = $1 AND season_id = $2"
            await ctx.db.execute(query, player.tag, season_id)
            return await ctx.confirm()

        query = "SELECT user_id FROM players WHERE player_tag = $1 AND season_id = $2"
        fetch = await ctx.db.fetchrow(query, player.tag, season_id)
        if not fetch:
            query = "UPDATE players SET user_id = NULL WHERE player_tag = $1 AND season_id = $2"
            await ctx.db.execute(query, player.tag, season_id)
            return await ctx.confirm()

        if fetch[0] != ctx.author.id:
            return await ctx.send(f'Player has been claimed by '
                                  f'{self.bot.get_user(fetch[0]) or "unknown"}.\n'
                                  f'Please contact them, or someone '
                                  f'with `manage_guild` permissions to unclaim it.')

        query = "UPDATE players SET user_id = NULL WHERE player_tag = $1 AND season_id = $2"
        await ctx.db.execute(query, player.tag, season_id)
        await ctx.confirm()

    @remove.command(name='donationboard', aliases=['donation board', 'donboard'])
    @checks.manage_guild()
    async def remove_donationboard(self, ctx, channel: discord.TextChannel = None):
        """Removes the guild donationboard.

        **Parameters**
        :key: A discord channel. If not present, it will use the channel you're currently in.

        **Format**
        :information_source: `+remove donationboard`
        :information_source: `+remove donationboard #CHANNEL`

        **Example**
        :white_check_mark: `+remove donationboard`
        :white_check_mark: `+remove donationboard #donationboard`

        **Required Permissions**
        :warning: Manage Server
        """
        await self.do_board_remove(ctx, channel or ctx.channel, "donation")

    @remove.command(name='trophyboard', aliases=['trophy board', 'tropboard'])
    @manage_guild()
    async def remove_trophyboard(self, ctx, channel: discord.TextChannel = None):
        """Removes a trophyboard.

        **Parameters**
        :key: A discord channel. If not present, it will use the channel you're currently in.

        **Format**
        :information_source: `+remove trophyboard`
        :information_source: `+remove trophyboard #CHANNEL`

        **Example**
        :white_check_mark: `+remove trophyboard`
        :white_check_mark: `+remove trophyboard #trophyboard`

        **Required Permissions**
        :warning: Manage Server
        """
        await self.do_board_remove(ctx, channel or ctx.channel, "trophy")

    async def do_board_remove(self, ctx, channel, type_):
        config = await self.bot.utils.board_config(channel.id)
        if not config:
            return await ctx.send(f"I couldn't find a {type_}board in {channel.mention}.")

        query = "DELETE FROM messages WHERE channel_id = $1"
        await ctx.db.execute(query, channel.id)

        try:
            await channel.delete(reason=f'Command done by {ctx.author} ({ctx.author.id})')
            msg = f"{type_}board sucessfully removed."
        except (discord.Forbidden, discord.HTTPException):
            msg = "I don't have permissions to delete the channel. Please manually delete it."

        query = """DELETE FROM boards WHERE channel_id = $1"""
        await self.bot.pool.execute(query, channel.id)
        query = "DELETE FROM clans WHERE channel_id = $1"
        await self.bot.pool.execute(query, channel.id)

        await ctx.send(msg)

    @remove.command(name='donationlog')
    @requires_config('donationlog', invalidate=True)
    @manage_guild()
    async def remove_donationlog(self, ctx, channel: TextChannel = None):
        """Removes a channel's donationlog.

        **Parameters**
        :key: A discord channel to remove the donationlog from.

        **Format**
        :information_source: `+remove donationlog #CHANNEL`

        **Example**
        :white_check_mark: `+remove donationlog #logging`

        **Required Permissions**
        :warning: Manage Server
        """
        if not ctx.config:
            return await ctx.send(f"No donationlog found for #{channel or ctx.channel}.")

        query = "DELETE FROM logs WHERE channel_id = $1 AND type = $2"
        await ctx.db.execute(query, ctx.config.channel_id, 'donation')
        await ctx.confirm()

    @remove.command(name='trophylog')
    @requires_config('trophylog', invalidate=True)
    @manage_guild()
    async def remove_trophylog(self, ctx, channel: TextChannel = None):
        """Removes a channel's trophylog.

        **Parameters**
        :key: A discord channel to remove the trophylog from.

        **Format**
        :information_source: `+remove trophylog #CHANNEL`

        **Example**
        :white_check_mark: `+remove trophylog #logging`

        **Required Permissions**
        :warning: Manage Server
        """
        if not ctx.config:
            return await ctx.send(f"No donationlog found for #{channel or ctx.channel}.")

        query = "DELETE FROM logs WHERE channel_id = $1 AND type = $2"
        await ctx.db.execute(query, ctx.config.channel_id, 'trophy')
        await ctx.confirm()

    @remove.command(name='event')
    @manage_guild()
    async def remove_event(self, ctx, *, event_name: str = None):
        """Removes a currently running event.

        **Parameters**
        :key: The event name to remove.

        **Format**
        :information_source: `+remove event EVENT_NAME`

        **Example**
        :white_check_mark: `+remove event my special event`

        **Required Permissions**
        :warning: Manage Server
        """
        if event_name:
            # Event name provided
            query = """DELETE FROM events
                       WHERE guild_id = $1 
                       AND event_name = $2
                       RETURNING id;
                    """
            fetch = await self.bot.pool.fetchrow(query, ctx.guild.id, event_name)
            if fetch:
                return await ctx.send(f"{event_name} has been removed.")

        # No event name provided or I didn't understand the name I was given
        query = """SELECT id, event_name, start 
                   FROM events
                   WHERE guild_id = $1 
                   ORDER BY start"""
        fetch = await self.bot.pool.fetch(query, ctx.guild.id)
        if len(fetch) == 0 or not fetch:
            return await ctx.send("I have no events to remove. You should create one... then remove it.")
        elif len(fetch) == 1:
            query = "DELETE FROM events WHERE id = $1"
            await ctx.db.execute(query, fetch[0]['id'])
            return await ctx.send(f"{fetch[0]['event_name']} has been removed.")

        table = CLYTable()
        fmt = f"Events on {ctx.guild}:\n\n"
        reactions = []
        counter = 0
        for event in fetch:
            days_until = event['start'].date() - datetime.datetime.utcnow().date()
            table.add_row([counter, days_until.days, event['event_name']])
            counter += 1
            reactions.append(f"{counter}\N{combining enclosing keycap}")
        render = table.events_list()
        fmt += f'{render}\n\nPlease select the reaction that corresponds with the event you would ' \
               f'like to remove.'
        e = discord.Embed(colour=self.bot.colour,
                          description=fmt)
        msg = await ctx.send(embed=e)
        for r in reactions:
            await msg.add_reaction(r)

        def check(r, u):
            return str(r) in reactions and u.id == ctx.author.id and r.message.id == msg.id

        try:
            r, u = await self.bot.wait_for('reaction_add', check=check, timeout=60.0)
        except asyncio.TimeoutError:
            await msg.clear_reactions()
            return await ctx.send("We'll just hang on to all the events we have for now.")

        index = reactions.index(str(r))
        query = "DELETE FROM events WHERE id = $1"
        await ctx.db.execute(query, fetch[index]['id'])
        await msg.delete()
        ctx.bot.utils.event_config.invalidate(ctx.bot.utils, ctx.guild.id)
        self.bot.dispatch('event_register')
        return await ctx.send(f"{fetch[index]['event_name']} has been removed.")


def setup(bot):
    bot.add_cog(Remove(bot))
