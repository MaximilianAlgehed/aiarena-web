# Generated by Django 3.0.8 on 2020-12-18 21:57

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

from aiarena.core.models import Competition
from aiarena.core.models.game import Game
from aiarena.core.models.game_type import GameMode


def update_name(apps, schema_editor):
    # set initial names
    for competition in Competition.objects.all():
        competition.name = f"AI Arena - Season {competition.id}"
        competition.save()

def create_game_mode(apps, schema_editor):
    game = Game.objects.create(name='StarCraft II')
    GameMode.objects.create(name='Melee', game=game)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_bot_wiki_article'),
    ]

    operations = [
        migrations.CreateModel(
            name='Game',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, unique=True)),
            ],
        ),
        migrations.RemoveField(
            model_name='map',
            name='active',
        ),
        migrations.RemoveField(
            model_name='season',
            name='number',
        ),
        migrations.RemoveField(
            model_name='season',
            name='previous_season_files_cleaned',
        ),
        migrations.AddField(
            model_name='map',
            name='competitions',
            field=models.ManyToManyField(related_name='maps', to='core.Season'),
        ),
        migrations.AddField(
            model_name='season',
            name='name',
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.AddField(
            model_name='season',
            name='type',
            field=models.CharField(choices=[('LRR', 'League - Round Robin')], default='LRR', max_length=32),
        ),
        migrations.AddField(
            model_name='seasonparticipation',
            name='active',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='season',
            name='status',
            field=models.CharField(blank=True, choices=[('created', 'Created'), ('paused', 'Paused'), ('open', 'Open'), ('closing', 'Closing'), ('closed', 'Closed')], default='created', max_length=16),
        ),
        migrations.AlterField(
            model_name='user',
            name='can_request_games_for_another_authors_bot',
            field=models.BooleanField(blank=True, default=False),
        ),
        migrations.AlterField(
            model_name='user',
            name='date_joined',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, verbose_name='date joined'),
        ),
        migrations.AlterField(
            model_name='user',
            name='extra_active_bots_per_race',
            field=models.IntegerField(blank=True, default=0),
        ),
        migrations.AlterField(
            model_name='user',
            name='extra_periodic_match_requests',
            field=models.IntegerField(blank=True, default=0),
        ),
        migrations.AlterField(
            model_name='user',
            name='patreon_level',
            field=models.CharField(blank=True, choices=[('none', 'None'), ('bronze', 'Bronze'), ('silver', 'Silver'), ('gold', 'Gold'), ('platinum', 'Platinum'), ('diamond', 'Diamond')], default='none', max_length=16),
        ),
        migrations.AlterField(
            model_name='user',
            name='receive_email_comms',
            field=models.BooleanField(blank=True, default=True),
        ),
        migrations.AlterField(
            model_name='user',
            name='sync_patreon_status',
            field=models.BooleanField(blank=True, default=True),
        ),
        migrations.CreateModel(
            name='GameMode',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50, unique=True)),
                ('game', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='game_modes', to='core.Game')),
            ],
        ),
        migrations.RunPython(update_name),
        migrations.RunPython(create_game_mode),
        migrations.AddField(
            model_name='season',
            name='game_mode',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='game_modes', to='core.GameMode'),
            preserve_default=False,
        ),
    ]
