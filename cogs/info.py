from discord.ext import commands
import discord
from .utils.paginator import Pages
import itertools
from datetime import datetime
from collections import Counter


class HelpPaginator(Pages):
    def __init__(self, help_command, ctx, entries, *, per_page=4):
        super().__init__(ctx, entries=entries, per_page=per_page)
        self.title = ''
        self.description = ''
        self.prefix = help_command.clean_prefix
        self.total = len(entries)
        self.help_command = help_command

    def get_bot_page(self, page):
        cog, description, commands = self.entries[page - 1]
        self.title = f'{cog} Commands'
        self.description = description
        return commands

    def prepare_embed(self, entries, page, *, first=False):
        self.embed.clear_fields()
        self.embed.description = f'{self.description}\n\u200b'
        self.embed.title = self.title

        self.embed.set_footer(text=f'Use "{self.prefix}help command" for more info on a command.')
        self.embed.timestamp = datetime.utcnow()

        for i, entry in enumerate(entries):
            sig = f'{self.help_command.get_command_signature(command=entry)}'
            fmt = entry.short_doc or "No help given"
            self.embed.add_field(name=sig, value=fmt + '\n\u200b' if i == (len(entries) - 1) else fmt, inline=False)

        self.embed.add_field(name='Support', value='Problem? Bug? Please join the support '
                                                   'server for more help: https://discord.gg/ePt8y4V')

        if self.maximum_pages:
            self.embed.set_author(name=f'Page {page}/{self.maximum_pages} ({self.total} commands)')


class HelpCommand(commands.HelpCommand):
    def get_command_signature(self, command):
        parent = command.full_parent_name
        if len(command.aliases) > 0:
            aliases = ', '.join(command.aliases)
            fmt = f'[aliases: {aliases}]'
            if parent:
                fmt = f'{self.clean_prefix}{parent} {fmt}'
            else:
                fmt = f'{self.clean_prefix}{command.name} {fmt}'
            alias = fmt
        else:
            alias = f'{self.clean_prefix}{parent} {command.name}'
        return alias

    async def send_bot_help(self, mapping):
        def key(c):
            return c.cog_name or '\u200bNo Category'

        bot = self.context.bot
        entries = await self.filter_commands(bot.commands, sort=True, key=key)
        nested_pages = []
        per_page = 9
        total = 0

        for cog, commands in itertools.groupby(entries, key=key):
            commands = sorted(commands, key=lambda c: c.name)
            if len(commands) == 0:
                continue

            total += len(commands)
            actual_cog = bot.get_cog(cog)
            # get the description if it exists (and the cog is valid) or return Empty embed.
            description = (actual_cog and actual_cog.description) or discord.Embed.Empty
            nested_pages.extend((cog, description, commands[i:i + per_page]) for i in range(0, len(commands), per_page))

        # a value of 1 forces the pagination session
        pages = HelpPaginator(self, self.context, entries=nested_pages, per_page=1)

        # swap the get_page implementation to work with our nested pages.
        pages.is_bot = True
        pages.total = total
        pages.get_page = pages.get_bot_page
        await self.context.release()
        await pages.paginate()

    async def send_cog_help(self, cog):
        entries = await self.filter_commands(cog.get_commands(), sort=True)
        pages = HelpPaginator(self, self.context, entries)
        pages.title = f'{cog.qualified_name} Commands'
        pages.description = f'{cog.description}\n\n'

        await self.context.release()
        await pages.paginate()

    def common_command_formatting(self, page_or_embed, command):
        page_or_embed.title = self.get_command_signature(command)
        if command.description:
            page_or_embed.description = f'{command.description}\n\n{command.help}'
        else:
            page_or_embed.description = command.help or 'No help found...'

    async def send_command_help(self, command):
        # No pagination necessary for a single command.
        embed = discord.Embed(colour=discord.Colour.blurple())
        self.common_command_formatting(embed, command)
        await self.context.send(embed=embed)

    async def send_group_help(self, group):
        subcommands = group.commands
        if len(subcommands) == 0:
            return await self.send_command_help(group)

        entries = await self.filter_commands(subcommands, sort=True)
        pages = HelpPaginator(self, self.context, entries)
        self.common_command_formatting(pages, group)

        await self.context.release()
        await pages.paginate()


class Info(commands.Cog):
    """Misc commands related to the bot."""
    def __init__(self, bot):
        self.bot = bot
        bot.help_command = HelpCommand()
        bot.help_command.cog = self

    @commands.command(aliases=['join'])
    async def invite(self, ctx):
        """Get an invite to add the bot to your server.
        """
        perms = discord.Permissions.none()
        perms.read_messages = True
        perms.external_emojis = True
        perms.send_messages = True
        perms.manage_channels = True
        perms.manage_messages = True
        perms.embed_links = True
        perms.read_message_history = True
        perms.add_reactions = True
        perms.attach_files = True
        await ctx.send(f'<{discord.utils.oauth_url(self.bot.client_id, perms)}>')

    @commands.group(hidden=True)
    async def info(self, ctx):
        pass

    async def send_guild_stats(self, e, guild):
        e.add_field(name='Name', value=guild.name)
        e.add_field(name='ID', value=guild.id)
        e.add_field(name='Owner', value=f'{guild.owner} (ID: {guild.owner.id})')

        bots = sum(m.bot for m in guild.members)
        total = guild.member_count
        online = sum(m.status is discord.Status.online for m in guild.members)
        e.add_field(name='Members', value=str(total))
        e.add_field(name='Bots', value=f'{bots} ({bots/total:.2%})')
        e.add_field(name='Online', value=f'{online} ({online/total:.2%})')

        if guild.icon:
            e.set_thumbnail(url=guild.icon_url)

        if guild.me:
            e.timestamp = guild.me.joined_at

        await self.bot.webhook.send(embed=e)

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        e = discord.Embed(colour=0x53dda4, title='New Guild')  # green colour
        await self.send_guild_stats(e, guild)
        query = "INSERT INTO guilds (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING"
        await self.bot.pool.execute(query, guild.id)

        if guild.system_channel:
            try:
                await guild.system_channel.send('Hi There! Thanks for adding my. My prefix is `+`, '
                                                'and all commands can be found with `+help`.'
                                                ' To start off, you might be looking for the `+updates` command, the '
                                                '`+log` command, the `+aclan` command and the `+auto_claim` command.\n\n'
                                                'Feel free to join the support server if you get stuck: discord.gg/ePt8y4V,'
                                                '\n\nHere is the invite link to share me with your friends: '
                                                'https://discordapp.com/oauth2/authorize?client_id=427301910291415051&'
                                                'scope=bot&permissions=388176. \n\nHave a good day!')
            except (discord.Forbidden, discord.HTTPException):
                pass
        else:
            for c in guild.channels:
                if c.permissions_for(c.guild.get_member(self.bot.user.id)).send_messages:
                    try:
                        await c.send('Hi There! Thanks for adding my. My prefix is `+`, '
                                     'and all commands can be found with `+help`.'
                                     ' To start off, you might be looking for the `+updates` command, the '
                                     '`+log` command, the `+aclan` command and the `+auto_claim` command.\n\n'
                                     'Feel free to join the support server if you get stuck: discord.gg/ePt8y4V,'
                                     '\n\nHere is the invite link to share me with your friends: '
                                     'https://discordapp.com/oauth2/authorize?client_id=427301910291415051&'
                                     'scope=bot&permissions=388176. \n\nHave a good day!')
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    return

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        e = discord.Embed(colour=0xdd5f53, title='Left Guild')  # red colour
        await self.send_guild_stats(e, guild)

    @commands.Cog.listener()
    async def on_command(self, ctx):
        command = ctx.command.qualified_name
        self.bot.command_stats[command] += 1
        message = ctx.message
        if ctx.guild is None:
            guild_id = None
        else:
            guild_id = ctx.guild.id

        query = """INSERT INTO commands (guild_id, channel_id, author_id, used, prefix, command)
                           VALUES ($1, $2, $3, $4, $5, $6)
                """

        await self.bot.pool.execute(query, guild_id, ctx.channel.id, ctx.author.id, message.created_at, ctx.prefix,
                                    command)


def setup(bot):
    if not hasattr(bot, 'command_stats'):
        bot.command_stats = Counter()

    bot.add_cog(Info(bot))
