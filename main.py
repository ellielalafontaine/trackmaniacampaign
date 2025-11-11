import discord
from discord.ext import commands
import os
import re
from typing import Optional, List, Dict
from supabase import create_client, Client
from flask import Flask
from threading import Thread

# Keep-alive web server for Render
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!", 200

@app.route('/health')
def health():
    return {"status": "alive", "bot": bot.user.name if bot.user else "starting"}, 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))  # Render provides PORT env var
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

def keep_alive():
    port = int(os.environ.get('PORT', 8080))
    print(f"🌐 Starting keep-alive web server on port {port}...")
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("✅ Web server thread started!")

# Bot configuration
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
LEADERBOARD_CHANNEL = int(os.getenv('LEADERBOARD_CHANNEL', '0'))

# Competition maps - 5 maps total
COMPETITION_MAPS = {
    1: "ZOOP 01",
    2: "Dirty Swervy 02", 
    3: "Cold and Mad 03",
    4: "Grassy Guy 04", 
    5: "Cold and Mad Pt 2 05"
}

class CampaignBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!tm ', intents=intents)
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.remove_command('help')

    async def setup_hook(self):
        print("🏁 Campaign Competition Bot is ready!")
        print(f"📊 Competition maps: {list(COMPETITION_MAPS.keys())}")

bot = CampaignBot()

@bot.event
async def on_ready():
    print(f'🤖 Bot logged in as {bot.user}!')

# ==================== PLAYER COMMANDS ====================

@bot.command(name='register')
async def register_player(ctx, *, trackmania_username: str):
    """Register for the campaign competition"""
    if len(trackmania_username) > 50:
        await ctx.send("❌ Username too long! Please use a shorter name.")
        return
    
    try:
        # Upsert player (insert or update if exists)
        bot.supabase.table('players').upsert({
            'discord_id': str(ctx.author.id),
            'tm_username': trackmania_username
        }).execute()
        
        await ctx.send(f"✅ Registered `{trackmania_username}` for {ctx.author.mention}!")
    except Exception as e:
        print(f"Error registering player: {e}")
        await ctx.send("❌ Registration failed. Please try again.")

@bot.command(name='time', aliases=['submit', 't'])
async def submit_time(ctx, map_num: int, *, time_str: str):
    """Submit a time for a map"""
    # Check if player is registered
    result = bot.supabase.table('players').select('tm_username').eq('discord_id', str(ctx.author.id)).execute()
    if not result.data:
        await ctx.send("❌ Please register first with `!tm register <your_trackmania_username>`")
        return

    # Check if valid map
    if map_num not in COMPETITION_MAPS:
        await ctx.send(f"❌ Invalid map! Choose from: {', '.join(map(str, COMPETITION_MAPS.keys()))}")
        return

    # Parse time
    time_ms = parse_time(time_str)
    if time_ms is None:
        await ctx.send("❌ Invalid time format! Use: `1:23.456`, `83.456`, or `83456` (ms)")
        return

    if not (1000 <= time_ms <= 600000):
        await ctx.send("❌ Time must be between 1 second and 10 minutes")
        return

    try:
        tm_username = result.data[0]['tm_username']
        
        # Upsert time (insert or update if exists)
        bot.supabase.table('times').upsert({
            'discord_id': str(ctx.author.id),
            'map_number': map_num,
            'time_ms': time_ms
        }).execute()

        formatted_time = format_time(time_ms)
        
        embed = discord.Embed(title="⏱️ Time Submitted!", color=discord.Color.green())
        embed.add_field(name="Player", value=tm_username, inline=True)
        embed.add_field(name="Map", value=f"Campaign {map_num:02d}", inline=True)
        embed.add_field(name="Time", value=formatted_time, inline=True)

        # Check position on map leaderboard
        position = await get_player_position(map_num, ctx.author.id)
        if position == 1:
            embed.add_field(name="🏆", value="First place!", inline=True)
        elif position <= 3:
            embed.add_field(name="🎯", value=f"#{position} on this map!", inline=True)

        await ctx.send(embed=embed)
        
    except Exception as e:
        print(f"Error submitting time: {e}")
        await ctx.send("❌ Failed to submit time. Please try again.")

@bot.command(name='leaderboard', aliases=['lb'])
async def show_leaderboard(ctx):
    """Show the full competition leaderboard"""
    try:
        description = "**Campaign Competition Leaderboard**\n\n"
        medals = ["🥇", "🥈", "🥉"]
        
        # Show each map's leaderboard
        for map_num in COMPETITION_MAPS.keys():
            map_lb = await get_map_leaderboard(map_num)
            
            description += f"**{COMPETITION_MAPS[map_num]}**\n"
            
            if not map_lb:
                description += "No times submitted\n\n"
                continue
            
            for i, entry in enumerate(map_lb[:10]):  # Top 10
                medal = medals[i] if i < 3 else f"#{i+1}"
                time_str = format_time(entry['time_ms'])
                
                split_text = ""
                if i > 0:
                    diff = entry['time_ms'] - map_lb[0]['time_ms']
                    split_text = f" (+{format_time(diff)})"
                
                description += f"{medal} {entry['tm_username']} — {time_str}{split_text}\n"
            
            description += "\n"
        
        # Overall standings (by points)
        overall = await get_overall_standings()
        if overall:
            description += "**Overall Standings**\n"
            for i, player in enumerate(overall[:10]):
                medal = medals[i] if i < 3 else f"#{i+1}"
                total_maps = len(COMPETITION_MAPS)
                description += f"{medal} {player['tm_username']} — {player['points']} pts ({player['maps_completed']}/{total_maps} maps)\n"
        
        embed = discord.Embed(
            title="🏁 Campaign Competition",
            description=description,
            color=discord.Color.green()
        )
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        print(f"Error showing leaderboard: {e}")
        await ctx.send("❌ Failed to load leaderboard.")

@bot.command(name='map')
async def show_map_leaderboard(ctx, map_num: int):
    """Show leaderboard for a specific map"""
    if map_num not in COMPETITION_MAPS:
        await ctx.send(f"❌ Invalid map! Choose from: {', '.join(map(str, COMPETITION_MAPS.keys()))}")
        return
    
    try:
        map_lb = await get_map_leaderboard(map_num)
        
        if not map_lb:
            await ctx.send(f"📊 No times submitted for {COMPETITION_MAPS[map_num]} yet!")
            return
        
        embed = discord.Embed(
            title=f"🗺️ {COMPETITION_MAPS[map_num]} Leaderboard",
            color=discord.Color.orange()
        )
        
        for i, entry in enumerate(map_lb[:10], 1):
            time_str = format_time(entry['time_ms'])
            
            if i == 1:
                display = f"⏱️ {time_str}"
            else:
                diff = entry['time_ms'] - map_lb[0]['time_ms']
                display = f"⏱️ {time_str} (+{format_time(diff)})"
            
            embed.add_field(
                name=f"#{i} - {entry['tm_username']}",
                value=display,
                inline=False
            )
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        print(f"Error showing map leaderboard: {e}")
        await ctx.send("❌ Failed to load map leaderboard.")

@bot.command(name='mystats', aliases=['me', 'stats'])
async def show_my_stats(ctx):
    """Show your personal stats"""
    try:
        result = bot.supabase.table('players').select('tm_username').eq('discord_id', str(ctx.author.id)).execute()
        if not result.data:
            await ctx.send("❌ You're not registered! Use `!tm register <username>`")
            return
        
        tm_username = result.data[0]['tm_username']
        
        # Get all player's times
        times_result = bot.supabase.table('times').select('*').eq('discord_id', str(ctx.author.id)).execute()
        
        embed = discord.Embed(
            title=f"📊 Stats for {tm_username}",
            color=discord.Color.blue()
        )
        
        total_points = 0
        for map_num in COMPETITION_MAPS.keys():
            player_time = next((t for t in times_result.data if t['map_number'] == map_num), None)
            
            if player_time:
                time_str = format_time(player_time['time_ms'])
                position = await get_player_position(map_num, ctx.author.id)
                points = get_points_for_position(position)
                total_points += points
                
                embed.add_field(
                    name=f"{COMPETITION_MAPS[map_num]}",
                    value=f"⏱️ {time_str}\n🏆 #{position} ({points} pts)",
                    inline=True
                )
            else:
                embed.add_field(
                    name=f"{COMPETITION_MAPS[map_num]}",
                    value="❌ No time",
                    inline=True
                )
        
        embed.add_field(
            name="📈 Total Score",
            value=f"**{total_points} points**",
            inline=False
        )
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        print(f"Error showing stats: {e}")
        await ctx.send("❌ Failed to load stats.")

@bot.command(name='help', aliases=['commands', 'h'])
async def show_help(ctx):
    """Show all commands"""
    embed = discord.Embed(
        title="🏁 Campaign Competition Commands",
        description="2-week competition on 5 Campaign maps",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="📝 Registration & Submission",
        value=(
            "`!tm register <username>` - Register for competition\n"
            "`!tm time <map> <time>` - Submit time (e.g. `!tm time 1 1:23.456`)"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📊 Leaderboards",
        value=(
            "`!tm leaderboard` - View full leaderboard\n"
            "`!tm map <number>` - View specific map (1-5)\n"
            "`!tm mystats` - View your personal stats"
        ),
        inline=False
    )
    
    embed.add_field(
        name="⏱️ Time Formats",
        value="`1:23.456` or `83.456` or `83456` (milliseconds)",
        inline=False
    )
    
    embed.add_field(
        name="🏆 Points System",
        value="1st: 25pts • 2nd: 18pts • 3rd: 15pts • 4th: 12pts • 5th: 10pts\nLower: 8, 6, 4, 2, 1",
        inline=False
    )
    
    await ctx.send(embed=embed)

# ==================== HELPER FUNCTIONS ====================

async def get_map_leaderboard(map_num: int) -> List[Dict]:
    """Get sorted leaderboard for a specific map"""
    result = bot.supabase.table('times').select('*, players(tm_username)').eq('map_number', map_num).execute()
    
    leaderboard = []
    for entry in result.data:
        leaderboard.append({
            'discord_id': entry['discord_id'],
            'tm_username': entry['players']['tm_username'],
            'time_ms': entry['time_ms']
        })
    
    return sorted(leaderboard, key=lambda x: x['time_ms'])

async def get_player_position(map_num: int, discord_id: int) -> int:
    """Get player's position on a map's leaderboard"""
    leaderboard = await get_map_leaderboard(map_num)
    for i, entry in enumerate(leaderboard, 1):
        if entry['discord_id'] == str(discord_id):
            return i
    return 0

async def get_overall_standings() -> List[Dict]:
    """Get overall standings based on points"""
    # Get all players
    players_result = bot.supabase.table('players').select('*').execute()
    
    standings = []
    for player in players_result.data:
        discord_id = player['discord_id']
        
        # Get player's times
        times_result = bot.supabase.table('times').select('*').eq('discord_id', discord_id).execute()
        
        total_points = 0
        maps_completed = len(times_result.data)
        
        # Calculate points from each map
        for time_entry in times_result.data:
            position = await get_player_position(time_entry['map_number'], int(discord_id))
            total_points += get_points_for_position(position)
        
        standings.append({
            'discord_id': discord_id,
            'tm_username': player['tm_username'],
            'points': total_points,
            'maps_completed': maps_completed
        })
    
    return sorted(standings, key=lambda x: (-x['points'], -x['maps_completed']))

def get_points_for_position(position: int) -> int:
    """Get points awarded for a leaderboard position"""
    point_values = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
    if position == 0:
        return 0
    if position <= len(point_values):
        return point_values[position - 1]
    return 1

def parse_time(time_str: str) -> Optional[int]:
    """Parse time string into milliseconds"""
    time_str = time_str.strip().replace(',', '.')

    # M:SS.mmm or M:SS:mmm
    match = re.match(r'^(\d+):(\d{1,2})[:.](\d{1,3})$', time_str)
    if match:
        minutes, seconds, ms = match.groups()
        ms = ms.ljust(3, '0')[:3]
        return int(minutes) * 60000 + int(seconds) * 1000 + int(ms)

    # SS.mmm
    match = re.match(r'^(\d+)\.(\d{1,3})$', time_str)
    if match:
        seconds, ms = match.groups()
        ms = ms.ljust(3, '0')[:3]
        return int(seconds) * 1000 + int(ms)

    # Raw milliseconds
    match = re.match(r'^(\d+)$', time_str)
    if match:
        return int(time_str)

    return None

def format_time(ms: int) -> str:
    """Format milliseconds as MM:SS.mmm"""
    if ms <= 0:
        return "00:00.000"
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    milliseconds = ms % 1000
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"

# ==================== RUN BOT ====================

if __name__ == "__main__":
    if not all([TOKEN, SUPABASE_URL, SUPABASE_KEY]):
        print("❌ Missing environment variables!")
        print("Required: DISCORD_BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY")
        exit(1)
    
    print("🚀 Starting Campaign Competition Bot...")
    keep_alive()  # Start web server for Render
    bot.run(TOKEN)
