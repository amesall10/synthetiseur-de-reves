"""Formulaires de gestion de compte."""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth.base_user import AbstractBaseUser


class DeleteAccountForm(forms.Form):
    """Demande le mot de passe avant de supprimer définitivement le compte.

    Attributes:
        password: Mot de passe actuel de l'utilisateur.
    """

    password = forms.CharField(
        label="Mot de passe actuel",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, user: AbstractBaseUser, *args: Any, **kwargs: Any) -> None:
        """Initialise le formulaire pour un utilisateur donné.

        Args:
            user: Utilisateur dont on vérifie le mot de passe.
            *args: Arguments positionnels transmis à ``forms.Form``.
            **kwargs: Arguments nommés transmis à ``forms.Form``.
        """
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self) -> str:
        """Vérifie que le mot de passe saisi est celui de l'utilisateur.

        Returns:
            Le mot de passe validé.

        Raises:
            forms.ValidationError: Si le mot de passe est incorrect.
        """
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Mot de passe incorrect.")
        return password
