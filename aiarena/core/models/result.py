
import logging
import time

from django.core.exceptions import ValidationError
from django.db import models

from aiarena.settings import ELO
from .match import Match
from .user import User
from .bot import Bot

logger = logging.getLogger(__name__)


def replay_file_upload_to(instance, filename):
    return '/'.join(['replays',
                     f'{instance.match_id}'
                     f'_{instance.match.matchparticipation_set.get(participant_number=1).bot.name}'
                     f'_{instance.match.matchparticipation_set.get(participant_number=2).bot.name}'
                     f'_{instance.match.map.name}.SC2Replay'])


def arenaclient_log_upload_to(instance, filename):
    return '/'.join(['arenaclient-logs', '{0}_arenaclientlog.zip'.format(instance.match_id)])


class Result(models.Model):
    TYPES = (
        ('MatchCancelled', 'MatchCancelled'),
        ('InitializationError', 'InitializationError'),
        ('Error', 'Error'),
        ('Player1Win', 'Player1Win'),
        ('Player1Crash', 'Player1Crash'),
        ('Player1TimeOut', 'Player1TimeOut'),
        ('Player1RaceMismatch', 'Player1RaceMismatch'),
        ('Player1Surrender', 'Player1Surrender'),
        ('Player2Win', 'Player2Win'),
        ('Player2Crash', 'Player2Crash'),
        ('Player2TimeOut', 'Player2TimeOut'),
        ('Player2RaceMismatch', 'Player2RaceMismatch'),
        ('Player2Surrender', 'Player2Surrender'),
        ('Tie', 'Tie'),
    )
    match = models.OneToOneField(Match, on_delete=models.CASCADE, related_name='result')
    winner = models.ForeignKey(Bot, on_delete=models.PROTECT, related_name='matches_won', blank=True, null=True)
    type = models.CharField(max_length=32, choices=TYPES)
    created = models.DateTimeField(auto_now_add=True)
    replay_file = models.FileField(upload_to=replay_file_upload_to, blank=True, null=True)
    game_steps = models.IntegerField()
    submitted_by = models.ForeignKey(User, on_delete=models.PROTECT, blank=True, null=True,
                                     related_name='submitted_results')
    arenaclient_log = models.FileField(upload_to=arenaclient_log_upload_to, blank=True, null=True)
    interest_rating = models.FloatField(blank=True, null=True)
    date_interest_rating_calculated = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.created.__str__()

    @property
    def duration_seconds(self):
        return (self.created - self.match.started).total_seconds()

    @property
    def game_time_formatted(self):
        return time.strftime("%H:%M:%S", time.gmtime(self.game_steps / 22.4))

    @property
    def participant1(self):
        return self.match.participant1

    @property
    def participant2(self):
        return self.match.participant2

    # this is not checked while the replay corruption is happening
    def validate_replay_file_requirement(self):
        if (self.has_winner() or self.is_tie()) and not self.replay_file:
            raise ValidationError('A win/loss or tie result must contain a replay file.')

    def clean(self, *args, **kwargs):
        # todo: if we ever re-enable this, then it needs to be
        # todo: called upon serializer validation in the arenaclient API
        # self.validate_replay_file_requirement() # disabled for now
        super().clean(*args, **kwargs)

    def has_winner(self):
        return self.type in (
            'Player1Win',
            'Player1Crash',
            'Player1TimeOut',
            'Player1Surrender',
            'Player2Win',
            'Player2Crash',
            'Player2TimeOut',
            'Player2Surrender')

    def winner_participant_number(self):
        if self.type in (
                'Player1Win',
                'Player2Crash',
                'Player2TimeOut',
                'Player2Surrender'):
            return 1
        elif self.type in (
                'Player1Crash',
                'Player1TimeOut',
                'Player1Surrender',
                'Player2Win'):
            return 2
        else:
            return 0

    def is_tie(self):
        return self.type == 'Tie'

    def is_timeout(self):
        return self.type == 'Player1TimeOut' or self.type == 'Player2TimeOut'

    def is_crash(self):
        return self.type == 'Player1Crash' or self.type == 'Player2Crash'

    def is_crash_or_timeout(self):
        return self.is_crash() or self.is_timeout()

    def get_causing_participant_of_crash_or_timeout_result(self):
        if self.type == 'Player1TimeOut' or self.type == 'Player1Crash':
            return self.participant1
        elif self.type == 'Player2TimeOut' or self.type == 'Player2Crash':
            return self.participant2
        else:
            return None

    def get_winner_loser_season_participants(self):
        bot1, bot2 = self.get_season_participants()
        if self.type in ('Player1Win', 'Player2Crash', 'Player2TimeOut', 'Player2Surrender'):
            return bot1, bot2
        elif self.type in ('Player2Win', 'Player1Crash', 'Player1TimeOut', 'Player1Surrender'):
            return bot2, bot1
        else:
            raise Exception('There was no winner or loser for this match.')

    def get_winner_loser_bots(self):
        bot1, bot2 = self.get_match_participant_bots()
        if self.type in ('Player1Win', 'Player2Crash', 'Player2TimeOut', 'Player2Surrender'):
            return bot1, bot2
        elif self.type in ('Player2Win', 'Player1Crash', 'Player1TimeOut', 'Player1Surrender'):
            return bot2, bot1
        else:
            raise Exception('There was no winner or loser for this match.')

    def get_season_participants(self):
        """Returns the SeasonParticipant models for the MatchParticipants"""
        from .match_participation import MatchParticipation
        first = MatchParticipation.objects.get(match=self.match, participant_number=1)
        second = MatchParticipation.objects.get(match=self.match, participant_number=2)
        return first.season_participant, second.season_participant

    def get_match_participants(self):
        from .match_participation import MatchParticipation
        first = MatchParticipation.objects.get(match=self.match, participant_number=1)
        second = MatchParticipation.objects.get(match=self.match, participant_number=2)
        return first, second

    def get_match_participant_bots(self):
        first, second = self.get_match_participants()
        return first.bot, second.bot

    def save(self, *args, **kwargs):
        from .match_participation import MatchParticipation
        # set winner
        if self.has_winner():
            self.winner = MatchParticipation.objects.get(match=self.match,
                                                         participant_number=self.winner_participant_number()).bot

        self.full_clean()  # ensure validation is run on save
        super().save(*args, **kwargs)

    def adjust_elo(self):
        if self.has_winner():
            sp_winner, sp_loser = self.get_winner_loser_season_participants()
            self._apply_elo_delta(ELO.calculate_elo_delta(sp_winner.elo, sp_loser.elo, 1.0), sp_winner, sp_loser)
        elif self.type == 'Tie':
            sp_first, sp_second = self.get_season_participants()
            self._apply_elo_delta(ELO.calculate_elo_delta(sp_first.elo, sp_second.elo, 0.5), sp_first, sp_second)

    def get_initial_elos(self):
        first, second = self.get_season_participants()
        return first.elo, second.elo

    def _apply_elo_delta(self, delta, sp1, sp2):
        delta = int(round(delta))
        sp1.elo += delta
        sp1.save()
        sp2.elo -= delta
        sp2.save()