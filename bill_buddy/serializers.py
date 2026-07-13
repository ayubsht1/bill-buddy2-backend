# serializers.py

from rest_framework import serializers
from .models import CustomUser

class RegisterSerializer(serializers.ModelSerializer):
    firstName = serializers.CharField(source='first_name', max_length=150)
    lastName = serializers.CharField(source='last_name', max_length=150)
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = CustomUser
        fields = ('email', 'firstName', 'lastName', 'password', 'username')
        # 🌟 FORCE DRF implicit unique validators to use your clean string
        extra_kwargs = {
            'email': {
                'error_messages': {
                    'unique': 'User already exists.'
                }
            },
            'username': {
                'error_messages': {
                    'unique': 'User already exists.'
                }
            }
        }

    def validate_email(self, value):
        if CustomUser.objects.filter(email=value).exists():
            raise serializers.ValidationError("User already exists.")
        return value

    def validate_username(self, value):
        if CustomUser.objects.filter(username=value).exists():
            raise serializers.ValidationError("User already exists.")
        return value

    def create(self, validated_data):
        user = CustomUser.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            is_active=False  # Require email verification
        )
        return user

class UserProfileSerializer(serializers.ModelSerializer):
    firstName = serializers.CharField(source='first_name', max_length=150, allow_blank=True, required=False)
    lastName = serializers.CharField(source='last_name', max_length=150, allow_blank=True, required=False)
    profilePicture = serializers.CharField(source='profile_picture', allow_blank=True, required=False)

    class Meta:
        model = CustomUser
        fields = ('id', 'email', 'username', 'firstName', 'lastName', 'profilePicture', 'is_active')
        read_only_fields = ('id', 'email', 'is_active')

class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=6)