import logging
import uuid
from datetime import timedelta
from enum import Enum

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from private_storage.fields import PrivateFileField

from aiarena.core.exceptions import BotNotInMatchException, BotAlreadyInMatchException
from aiarena.core.storage import OverwritePrivateStorage
from aiarena.core.utils import calculate_md5
from aiarena.settings import ELO_START_VALUE, MAX_USER_BOT_COUNT, MAX_USER_BOT_COUNT_ACTIVE_PER_RACE

logger = logging.getLogger(__name__)


class Map(models.Model):
    name = models.CharField(max_length=50, unique=True)
    file = models.FileField(upload_to='maps')

    def __str__(self):
        return self.name

    @staticmethod
    def random():
        # todo: apparently this is really slow
        # https://stackoverflow.com/questions/962619/how-to-pull-a-random-record-using-djangos-orm#answer-962672
        return Map.objects.order_by('?').first()


class User(AbstractUser):
    email = models.EmailField(unique=True)
    service_account = models.BooleanField(default=False)

    def get_absolute_url(self):
        return reverse('author', kwargs={'pk': self.pk})

    def as_html_link(self):
        return '<a href="{0}">{1}</a>'.format(self.get_absolute_url(), escape(self.__str__()))


class Round(models.Model):
    started = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(blank=True, null=True)
    complete = models.BooleanField(default=False)

    # if all the matches have been run, mark this as complete
    def update_if_completed(self):
        # if these are no matches without results, this round is complete
        if Match.objects.filter(round=self, result__isnull=True).count() == 0:
            self.complete = True
            self.finished = timezone.now()
            self.save()


# todo: structure for separate ladder types
class Match(models.Model):
    map = models.ForeignKey(Map, on_delete=models.PROTECT)
    created = models.DateTimeField(auto_now_add=True)
    started = models.DateTimeField(blank=True, null=True, editable=False)
    assigned_to = models.ForeignKey(User, on_delete=models.PROTECT, blank=True, null=True)
    round = models.ForeignKey(Round, on_delete=models.PROTECT)

    def __str__(self):
        return self.id.__str__()

    class StartResult(Enum):
        SUCCESS = 1
        FAIL_BOT_ALREADY_IN_MATCH = 2
        FAIL_ALREADY_STARTED = 3

    class CancelResult(Enum):
        SUCCESS = 1
        FAIL_ALREADY_HAS_RESULT = 2

    def start(self, assign_to):
        with transaction.atomic():
            Match.objects.select_for_update().get(id=self.id)  # lock self to avoid race conditions
            if self.started is None:
                # is a bot currently in a match?
                participants = Participant.objects.select_for_update().filter(match=self)
                for p in participants:
                    if p.bot.in_match:
                        return Match.StartResult.FAIL_BOT_ALREADY_IN_MATCH

                for p in participants:
                    p.bot.enter_match(self)

                self.started = timezone.now()
                self.assigned_to = assign_to
                self.save()
                return Match.StartResult.SUCCESS
            else:
                return Match.StartResult.FAIL_ALREADY_STARTED

    def cancel(self):
        with transaction.atomic():
            # IMPORTANT before we do anything, lock the match in question to avoid race conditions
            # using this convention we try to avoid having to to lock the entire result table
            match = Match.objects.select_for_update().get(id=self.id)

            if Result.objects.filter(match=self).count() > 0:
                return Match.CancelResult.FAIL_ALREADY_HAS_RESULT

            Result.objects.create(match=self, type='MatchCancelled', duration=0)

            # attempt to kick the bots from the match
            if match.started:
                bot1 = match.participant_set.select_related().select_for_update().get(participant_number=1).bot
                try:
                    bot1.leave_match(match.id)
                except BotNotInMatchException:
                    logger.warning(
                        'WARNING! Match "{1}": Participant 1 bot "{0}" was not registered as in this match, despite the match having started.'.format(
                            bot1.id, match.id))

                bot2 = match.participant_set.select_related().select_for_update().get(participant_number=2).bot
                try:
                    bot2.leave_match(match.id)
                except BotNotInMatchException:
                    logger.warning(
                        'WARNING! Match "{1}": Participant 2 bot "{0}" was not registered as in this match, despite the match having started.'.format(
                            bot2.id, match.id))
            else:
                match.started = timezone.now()
                match.save()

            return Match.CancelResult.SUCCESS

    # todo: have arena client check in with web service inorder to delay this
    @staticmethod
    def timeout_overtime_matches():
        # get matches started over an hour ago
        matches_to_timeout = Match.objects.filter(started__lt=timezone.now() - timedelta(hours=1)).select_for_update()
        for match in matches_to_timeout:
            match.cancel()


    @staticmethod
    def create(round, map, bot1, bot2):
        match = Match.objects.create(map=map, round=round)
        # create match participants
        Participant.objects.create(match=match, participant_number=1, bot=bot1)
        Participant.objects.create(match=match, participant_number=2, bot=bot2)


def bot_zip_upload_to(instance, filename):
    return '/'.join(['bots', str(instance.id), 'bot_zip'])


def bot_data_upload_to(instance, filename):
    return '/'.join(['bots', str(instance.id), 'bot_data'])


class Bot(models.Model):
    RACES = (
        ('T', 'Terran'),
        ('Z', 'Zerg'),
        ('P', 'Protoss'),
        ('R', 'Random'),
    )
    TYPES = (
        ('cppwin32', 'cppwin32'),
        ('cpplinux', 'cpplinux'),
        ('dotnetcore', 'dotnetcore'),
        ('java', 'java'),
        ('nodejs', 'nodejs'),
        ('python', 'python'),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bots')
    name = models.CharField(max_length=50, unique=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    active = models.BooleanField(default=False)  # todo: change this to instead be an enrollment in a ladder?
    in_match = models.BooleanField(default=False)  # todo: move to ladder participant when multiple ladders comes in
    current_match = models.ForeignKey(Match, on_delete=models.PROTECT, blank=True, null=True,
                                      related_name='bots_currently_in_match')
    elo = models.SmallIntegerField(default=ELO_START_VALUE)
    bot_zip = PrivateFileField(upload_to=bot_zip_upload_to, storage=OverwritePrivateStorage(base_url='/'),
                               max_file_size=1024 * 1024 * 50)  # max_file_size = 50MB
    bot_zip_md5hash = models.CharField(max_length=32, editable=False)
    bot_zip_publicly_downloadable = models.BooleanField(default=False)
    # todo: set a file size limit which will be checked on result submission
    # and the bot deactivated if the file size exceeds it
    bot_data = PrivateFileField(upload_to=bot_data_upload_to, storage=OverwritePrivateStorage(base_url='/'),
                                blank=True, null=True)
    bot_data_md5hash = models.CharField(max_length=32, editable=False, null=True)
    bot_data_publicly_downloadable = models.BooleanField(default=False)
    plays_race = models.CharField(max_length=1, choices=RACES)
    type = models.CharField(max_length=32, choices=TYPES)
    # the ID displayed to other bots during a game so they can recognize their opponent
    game_display_id = models.UUIDField(default=uuid.uuid4)

    def calc_bot_files_md5hash(self):
        # we technically shouldn't be accessing _committed, but it was the only way I could find to know
        # when the bot_zip was actually saved so I could load it off the disk
        # todo: probably shouldn't be loading via the path?
        # todo: attempt with django filefield open, and close it during rename phase?
        if self.bot_zip._committed:
            self.bot_zip_md5hash = calculate_md5(self.bot_zip.path)

        if self.bot_data and self.bot_data._committed:
            self.bot_data_md5hash = calculate_md5(self.bot_data.path)

    # todo: once multiple ladders comes in, this will need to be updated to 1 bot race per ladder per user.
    def validate_active_bot_race_per_user(self):
        # if there is already an active bot for this user playing the same race, and this bot is also marked as active
        # then back out
        if Bot.objects.filter(user=self.user, active=True, plays_race=self.plays_race).exclude(
                id=self.id).count() >= MAX_USER_BOT_COUNT_ACTIVE_PER_RACE \
                and self.active:
            raise ValidationError(
                'Too many active bots playing that race already exist for this user.'
                ' Each user can only have ' + str(MAX_USER_BOT_COUNT_ACTIVE_PER_RACE) + ' active bot(s) per race.')

    def validate_max_bot_count(self):
        if Bot.objects.filter(user=self.user).exclude(id=self.id).count() >= MAX_USER_BOT_COUNT:
            raise ValidationError(
                'Maximum bot count of {0} already reached. No more bots may be added for this user.'.format(
                    MAX_USER_BOT_COUNT))

    def save(self, *args, **kwargs):
        self.calc_bot_files_md5hash()
        super(Bot, self).save(*args, **kwargs)

    def clean(self):
        self.validate_max_bot_count()
        self.validate_active_bot_race_per_user()

    def __str__(self):
        return self.name

    def enter_match(self, match):
        if not self.in_match:
            self.current_match = match
            self.in_match = True
            self.save()
        else:
            raise BotAlreadyInMatchException('Cannot enter a match - bot is already in one.')

    def leave_match(self, match_id=None):
        if self.in_match and (match_id is None or self.current_match_id == match_id):
            self.current_match = None
            self.in_match = False
            self.save()
        else:
            if match_id is None:
                raise BotNotInMatchException('Cannot leave match - bot is not in one.')
            else:
                raise BotNotInMatchException('Cannot leave match - bot is not in match "{0}".'.format(match_id))

    def bot_data_is_currently_frozen(self):
        # dont alter bot_data while a bot is in a match, unless there was no bot_data initially
        return self.in_match and self.bot_data

    @staticmethod
    def get_random_available():
        # todo: apparently this is really slow
        # https://stackoverflow.com/questions/962619/how-to-pull-a-random-record-using-djangos-orm#answer-962672
        return Bot.objects.filter(active=True, in_match=False).order_by('?').first()

    @staticmethod
    def active_count():
        return Bot.objects.filter(active=True).count()

    def get_random_available_excluding_self(self):
        if Bot.active_count() <= 1:
            raise RuntimeError("I am the only bot.")
        return Bot.objects.filter(active=True, in_match=False).exclude(id=self.id).order_by('?').first()

    def get_absolute_url(self):
        return reverse('bot', kwargs={'pk': self.pk})

    def as_html_link(self):
        return '<a href="{0}">{1}</a>'.format(self.get_absolute_url(), escape(self.__str__()))


_UNSAVED_BOT_ZIP_FILEFIELD = 'unsaved_bot_zip_filefield'
_UNSAVED_BOT_DATA_FILEFIELD = 'unsaved_bot_data_filefield'


# The following methods will temporarily store the bot_zip and bot_data files while we wait for the Bot model to be
# saved in order to generate an ID, which can then be used in the path for the bot_zip name
@receiver(pre_save, sender=Bot)
def skip_saving_bot_files(sender, instance, **kwargs):
    # bot zip
    if not instance.pk and not hasattr(instance, _UNSAVED_BOT_ZIP_FILEFIELD):
        setattr(instance, _UNSAVED_BOT_ZIP_FILEFIELD, instance.bot_zip)
        instance.bot_zip = None

    # bot data
    if not instance.pk and not hasattr(instance, _UNSAVED_BOT_DATA_FILEFIELD) and instance.bot_data:
        setattr(instance, _UNSAVED_BOT_DATA_FILEFIELD, instance.bot_data)
        instance.bot_data = None


@receiver(post_save, sender=Bot)
def save_bot_files(sender, instance, created, **kwargs):
    # bot zip
    if created and hasattr(instance, _UNSAVED_BOT_ZIP_FILEFIELD):
        instance.bot_zip = getattr(instance, _UNSAVED_BOT_ZIP_FILEFIELD)
        instance.save()  # so the file is saved to disk
        instance.bot_zip_md5hash = calculate_md5(instance.bot_zip.path)
        instance.save()
        # delete the saved instance
        instance.__dict__.pop(_UNSAVED_BOT_ZIP_FILEFIELD)

    # bot data
    if created and hasattr(instance, _UNSAVED_BOT_DATA_FILEFIELD):
        instance.bot_data = getattr(instance, _UNSAVED_BOT_DATA_FILEFIELD)
        instance.save()  # so the file is saved to disk
        instance.bot_data_md5hash = calculate_md5(instance.bot_data.path)
        instance.save()
        # delete the saved instance
        instance.__dict__.pop(_UNSAVED_BOT_DATA_FILEFIELD)


class Participant(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE)
    participant_number = models.PositiveSmallIntegerField()
    bot = models.ForeignKey(Bot, on_delete=models.PROTECT, related_name='match_participations')
    resultant_elo = models.SmallIntegerField(null=True)
    elo_change = models.SmallIntegerField(null=True)

    def update_resultant_elo(self):
        self.resultant_elo = self.bot.elo
        self.save()

    def __str__(self):
        return self.bot.name


class Result(models.Model):
    TYPES = (
        ('MatchCancelled', 'MatchCancelled'),
        ('InitializationError', 'InitializationError'),
        ('Timeout', 'Timeout'),
        ('ProcessingReplay', 'ProcessingReplay'),
        ('Player1Win', 'Player1Win'),
        ('Player1Crash', 'Player1Crash'),
        ('Player1TimeOut', 'Player1TimeOut'),
        ('Player2Win', 'Player2Win'),
        ('Player2Crash', 'Player2Crash'),
        ('Player2TimeOut', 'Player2TimeOut'),
        ('Tie', 'Tie'),
        ('Error', 'Error'),
    )
    match = models.OneToOneField(Match, on_delete=models.CASCADE, related_name='result')
    winner = models.ForeignKey(Bot, on_delete=models.PROTECT, related_name='matches_won', blank=True, null=True)
    type = models.CharField(max_length=32, choices=TYPES)
    created = models.DateTimeField(auto_now_add=True)
    replay_file = models.FileField(upload_to='replays', blank=True, null=True)
    duration = models.IntegerField()
    submitted_by = models.ForeignKey(User, on_delete=models.PROTECT, blank=True, null=True)

    def __str__(self):
        return self.created.__str__()

    # this is not checked while the replay corruption is happening
    def validate_replay_file_requirement(self):
        if (self.has_winner() or self.is_tie()) and not self.replay_file:
            raise ValidationError('A win/loss or tie result must contain a replay file.')

    def clean(self, *args, **kwargs):
        # self.validate_replay_file_requirement() # disabled for now
        super().clean(*args, **kwargs)

    # some replays corrupt under linux currently.
    # if a replay file isn't supplied when it should be, then we assume it was corrupted
    # and therefore not uploaded with the result.
    def replay_file_corruption_detected(self):
        return (self.has_winner() or self.is_tie()) and not self.replay_file

    def has_winner(self):
        return self.type in (
            'Player1Win',
            'Player1Crash',
            'Player1TimeOut',
            'Player2Win',
            'Player2Crash',
            'Player2TimeOut')

    def winner_participant_number(self):
        if self.type in (
                'Player1Win',
                'Player2Crash',
                'Player2TimeOut'):
            return 1
        elif self.type in (
                'Player1Crash',
                'Player1TimeOut',
                'Player2Win'):
            return 2
        else:
            return 0

    def is_tie(self):
        return self.type == 'Tie'

    def get_winner_loser_bots(self):
        bot1, bot2 = self.get_participant_bots()
        if self.type in ('Player1Win', 'Player2Crash', 'Player2TimeOut'):
            return bot1, bot2
        elif self.type in ('Player2Win', 'Player1Crash', 'Player1TimeOut'):
            return bot2, bot1
        else:
            raise Exception('There was no winner or loser for this match.')

    def get_participants(self):
        first = Participant.objects.filter(match=self.match, participant_number=1)
        second = Participant.objects.filter(match=self.match, participant_number=2)

        assert (first.count() == 1)
        assert (second.count() == 1)
        return first[0], second[0]

    def get_participant_bots(self):
        first, second = self.get_participants()
        return first.bot, second.bot

    def save(self, *args, **kwargs):
        # set winner
        if self.has_winner():
            self.winner = Participant.objects.get(match=self.match,
                                                  participant_number=self.winner_participant_number()).bot

        self.full_clean()  # ensure validation is run on save
        super().save(*args, **kwargs)

    # todo: validate that if the result type is either a timeout or tie, then there's no winner set etc
    # todo: use a model form
