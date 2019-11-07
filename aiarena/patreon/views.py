import json
import logging
import traceback
import urllib.parse

from constance import config
from django.contrib import messages
from django.contrib.sites.models import Site
from django.shortcuts import redirect
from django.views import View

from aiarena.patreon.models import PatreonAccountBind
from aiarena.patreon.patreon import PatreonOAuth, PatreonApi

logger = logging.getLogger(__name__)


class PatreonBindView(View):
    def get(self, request):
        site = Site.objects.get_current()
        domain = urllib.parse.quote_plus(site.domain)
        return redirect("https://www.patreon.com/oauth2/authorize"
                        "?response_type=code"
                        "&client_id=" + config.PATREON_CLIENT_ID +
                        "&redirect_uri=https%3A%2F%2F" + domain + "%2Fpatreon%2Foauth%2Fredirect")


class PatreonCallbackView(View):
    def get(self, request):
        oauth_client = PatreonOAuth(config.PATREON_CLIENT_ID, config.PATREON_CLIENT_SECRET)
        site = Site.objects.get_current()
        tokens = oauth_client.get_tokens(request.GET['code'], 'https://' + site.domain + '/patreon/oauth/redirect')

        if 'access_token' in tokens and 'refresh_token' in tokens:
            account_bind, created = PatreonAccountBind.objects.get_or_create(user=request.user)
            account_bind.access_token = tokens['access_token']
            account_bind.refresh_token = tokens['refresh_token']
            account_bind.save()
            messages.add_message(request, messages.SUCCESS, 'Patreon successfully linked.')

            try:
                api_client = PatreonApi(account_bind.access_token)
                user = api_client.current_user()
                patreon_level = 'none'
                for entry in user['included']:
                    if entry['type'] == 'pledge':
                        if entry['attributes']['amount_cents'] >= 10000:  # diamond
                            patreon_level = 'diamond'
                            break
                        elif entry['attributes']['amount_cents'] >= 5000:  # platinum
                            patreon_level = 'platinum'
                            break
                        elif entry['attributes']['amount_cents'] >= 2500:  # gold
                            patreon_level = 'gold'
                            break
                        elif entry['attributes']['amount_cents'] >= 1000:  # silver
                            patreon_level = 'silver'
                            break
                        elif entry['attributes']['amount_cents'] >= 500:  # bronze
                            patreon_level = 'bronze'
                            break
                        else:
                            raise Exception("Unexpected Patreon tier encountered!")

                request.user.patreon_level = patreon_level
                request.user.save()
            except Exception as e:
                logger.error("Failed to update patreon tier with error:\n" + traceback.print_exc())
                messages.add_message(request, messages.WARNING, 'There was an issue updating your account\'s Patreon tier.')

        else:
            logger.error("Patreon linkage failed. Tokens dump:\n" + json.dumps(tokens))
            messages.add_message(request, messages.ERROR,
                                 'Sorry, there was an issue linking your patreon. Please try again later. If this issue persists, please contact an admin via discord.')

        return redirect('profile')