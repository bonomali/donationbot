import math

from coc.utils import correct_tag
from discord.ext import commands

from cogs.utils.paginator import (
    StatsAttacksPaginator, StatsDefensesPaginator, StatsGainsPaginator, StatsDonorsPaginator
)
from cogs.utils.emoji_lookup import misc


class Stats(commands.Cog):
    """Redirect stats commands to the appropriate place"""
    def __init__(self, bot):
        self.bot = bot

    async def get_players(self, ctx, clan_tag_or_name, extra_columns=None, order_by=None):
        extra_columns = extra_columns or []
        if clan_tag_or_name:
            query = f"""
            WITH cte AS (
                SELECT DISTINCT clan_tag
                FROM clans
                WHERE clan_tag = $1
                OR clan_name LIKE $2
            )
            SELECT DISTINCT player_tag {', '.join(extra_columns)}
            FROM players 
            INNER JOIN cte
            ON cte.clan_tag = players.clan_tag
            ORDER BY {order_by or 'player_tag'} 
            NULLS LAST
            """
            return await ctx.db.fetch(query, correct_tag(clan_tag_or_name), clan_tag_or_name)
        else:
            query = f"""SELECT DISTINCT player_tag {', '.join(extra_columns)} 
                        FROM players 
                        INNER JOIN clans 
                        ON clans.clan_tag = players.clan_tag 
                        WHERE clans.channel_id = $1
                        ORDER BY {order_by or 'player_tag'}
                        NULLS LAST
                    """
            return await ctx.db.fetch(query, ctx.channel.id)

    @commands.group(invoke_without_command=True)
    async def stats(self, ctx):
        """The main stats command for all donation, trophy, attacks and defense statistics.

        This command does nothing by itself, however - check out the subcommands!
        """
        if ctx.invoked_subcommand is None:
            return await ctx.send_help(ctx.command)

    @stats.command(name='attacks')
    async def stats_attacks(self, ctx, *, clan_tag_or_name: str = None):
        """Get top attack wins for clan(s).

        **Parameters**
        :key: Clan tag or name. Defaults to clans added to this channel.

        **Format**
        :information_source: `+stats attacks`
        :information_source: `+stats attacks #CLAN_TAG`
        :information_source: `+stats attacks CLAN NAME`

        **Example**
        :white_check_mark: `+stats attacks`
        :white_check_mark: `+stats attacks #JY9J2Y99`
        :white_check_mark: `+stats attacks Reddit`
        """
        fetch = await self.get_players(ctx, clan_tag_or_name)
        title = f"Attack Wins"
        key = f"**Key:**\n{misc['attack']} - Attacks"

        p = StatsAttacksPaginator(ctx, fetch, title, key=key, page_count=math.ceil(len(fetch) / 20))
        await p.paginate()

    @stats.command(name='defenses', aliases=['defense', 'defences', 'defence'])
    async def stats_defenses(self, ctx, *, clan_tag_or_name: str = None):
        """Get top defense wins for clan(s).

        **Parameters**
        :key: Clan tag or name. Defaults to clans added to this channel.

        **Format**
        :information_source: `+stats defenses`
        :information_source: `+stats defenses #CLAN_TAG`
        :information_source: `+stats defenses CLAN NAME`

        **Example**
        :white_check_mark: `+stats defenses`
        :white_check_mark: `+stats defenses #JY9J2Y99`
        :white_check_mark: `+stats defenses Reddit`
        """
        fetch = await self.get_players(ctx, clan_tag_or_name)
        title = f"Defense Wins"
        key = f"**Key:**\n{misc['defense']} - Defenses"

        p = StatsDefensesPaginator(ctx, fetch, title, key=key, page_count=math.ceil(len(fetch) / 20))
        await p.paginate()

    @stats.command(name='gains', aliases=['gain', 'trophies'])
    async def stats_gains(self, ctx, *, clan_tag_or_name: str = None):
        """Get top trophy gainers for clan(s).

        **Parameters**
        :key: Clan tag or name. Defaults to clans added to this channel.

        **Format**
        :information_source: `+stats defenses`
        :information_source: `+stats defenses #CLAN_TAG`
        :information_source: `+stats defenses CLAN NAME`

        **Example**
        :white_check_mark: `+stats defenses`
        :white_check_mark: `+stats defenses #JY9J2Y99`
        :white_check_mark: `+stats defenses Reddit`
        """
        fetch = await self.get_players(ctx, clan_tag_or_name, extra_columns=['trophies - start_trophies AS "gain"'])
        title = f"Top Trophy Gains"
        key = f"**Key:**\n{misc['trophygreen']} - Trophy Gain\n{misc['trophygold']} - Total Trophies"

        p = StatsGainsPaginator(ctx, fetch, title, key=key, page_count=math.ceil(len(fetch) / 20))
        await p.paginate()

    @stats.command(name='donations', aliases=['donates', 'donation', 'donors'])
    async def stats_donations(self, ctx, *, clan_tag_or_name: str = None):
        """Get top donators for clan(s).

        **Parameters**
        :key: Clan tag or name. Defaults to clans added to this channel.

        **Format**
        :information_source: `+stats donations`
        :information_source: `+stats donations #CLAN_TAG`
        :information_source: `+stats donations CLAN NAME`

        **Example**
        :white_check_mark: `+stats donations`
        :white_check_mark: `+stats donations #JY9J2Y99`
        :white_check_mark: `+stats donations Reddit`
        """
        fetch = await self.get_players(ctx, clan_tag_or_name, extra_columns=["donations"], order_by="donations")
        title = "Top Donations"

        p = StatsDonorsPaginator(ctx, fetch, title, page_count=math.ceil(len(fetch) / 20))
        await p.paginate()


def setup(bot):
    bot.add_cog(Stats(bot))
